#!/usr/bin/env python3
"""Dominic Spark: mini AI agent as OpenAI-compatible endpoint (stdlib only).

GET  /health                 -> {"ok": true}
GET  /v1/models              -> lists dominic-spark (needs user key)
POST /v1/chat/completions    -> ReAct loop: backend LLM + local tools
All /v1/* need: Authorization: Bearer <key in key.txt>

Backend = 9Router local (default model agnes/agnes-3.0-flash).
Tools: read_file, write_file, run_shell (jailed in WORKDIR).
"""
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE, "key.txt")
PORT = int(os.environ.get("DOM_PORT", "8766"))
WORKDIR = os.environ.get("DOM_WORKDIR", r"C:\Users\ridwan\Documents\Default Project")
BACKEND_URL = os.environ.get("DOM_BACKEND_URL", "http://127.0.0.1:20128/v1")
BACKEND_MODEL = os.environ.get("DOM_BACKEND_MODEL", "agnes/agnes-3.0-flash")
MODEL_ID = "dominic-spark"
MAX_STEPS = 8
MAX_BODY = 10 * 1024 * 1024


def log(*a):
    print(*a, flush=True)


def get_key():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            return f.read().strip()
    key = "dom_" + secrets.token_urlsafe(24)
    with open(KEY_FILE, "w") as f:
        f.write(key)
    return key


AGENT_KEY = get_key()


def nine_router_key():
    db = os.path.join(os.environ["APPDATA"], "9router", "db", "data.sqlite")
    import sqlite3
    con = sqlite3.connect(db)
    key = con.execute("SELECT key FROM apiKeys LIMIT 1").fetchone()[0]
    con.close()
    return key


BACKEND_KEY = nine_router_key()

SYSTEM = (
    "You are Dominic Spark, an AI agent running on the user's own computer. "
    f"You can act on files under the working directory using tools. "
    "Respond with EXACTLY ONE of:\n"
    'ACTION: {"tool": "read_file", "path": "relative/path"}\n'
    'ACTION: {"tool": "write_file", "path": "relative/path", "content": "full text"}\n'
    'ACTION: {"tool": "run_shell", "command": "shell command"}\n'
    "FINAL: <your final answer to the user>\n"
    "No other text outside these formats. After an ACTION you get an OBSERVATION, "
    "then continue until you can give FINAL."
)


def safe_path(p):
    full = os.path.normpath(os.path.join(WORKDIR, p))
    if os.path.commonpath([full, WORKDIR]) != WORKDIR:
        raise ValueError("path escapes working directory")
    return full


def run_tool(tool, args):
    try:
        if tool == "read_file":
            with open(safe_path(args["path"]), encoding="utf-8", errors="replace") as f:
                return f.read()[:20000]
        if tool == "write_file":
            fp = safe_path(args["path"])
            os.makedirs(os.path.dirname(fp) or WORKDIR, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(args.get("content", ""))
            return "written " + args["path"]
        if tool == "run_shell":
            p = subprocess.run(args["command"], shell=True, cwd=WORKDIR,
                               capture_output=True, text=True, timeout=120)
            return f"exit={p.returncode}\nSTDOUT:\n{p.stdout[-8000:]}\nSTDERR:\n{p.stderr[-4000:]}"
        return "unknown tool: " + tool
    except Exception as e:
        return "tool error: " + str(e)[:500]


def backend_chat(messages, max_tokens=1024):
    t0 = time.time()
    body = json.dumps({"model": BACKEND_MODEL, "messages": messages,
                       "max_tokens": max_tokens, "stream": False}).encode()
    req = urllib.request.Request(BACKEND_URL + "/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + BACKEND_KEY)
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read().decode().replace("data: [DONE]", "").strip()
    log(f"[timing] backend_chat took {time.time()-t0:.1f}s")
    return json.loads(raw)["choices"][0]["message"]["content"]


def agent_answer(user_messages, max_tokens):
    t0 = time.time()
    msgs = [{"role": "system", "content": SYSTEM}] + [m for m in user_messages if m.get("role") in ("user", "system")]
    trace = []
    for _ in range(MAX_STEPS):
        try:
            out = backend_chat(msgs, max_tokens)
        except Exception as e:
            return f"Dominic Spark: backend error ({str(e)[:200]}). Trace: {' | '.join(trace)}"
        m = re.search(r"ACTION:\s*(\{.*\})", out, re.S)
        if m:
            try:
                act = json.loads(m.group(1))
                obs = run_tool(act.get("tool", ""), act)
            except Exception as e:
                obs = "action parse error: " + str(e)[:200]
            trace.append(f"{act.get('tool', '?')} -> {obs[:80]}")
            msgs.append({"role": "assistant", "content": out})
            msgs.append({"role": "user", "content": "OBSERVATION:\n" + obs})
            continue
        f = re.search(r"FINAL:\s*(.*)", out, re.S)
        log(f"[timing] agent_answer took {time.time()-t0:.1f}s")
        return (f.group(1).strip() if f else out.strip())
    return "Dominic Spark: max steps reached. Trace: " + " | ".join(trace)


def completion(cid, text):
    return {"id": cid, "object": "chat.completion", "created": int(time.time()),
            "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


class H(BaseHTTPRequestHandler):
    timeout = 120

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass

    def _auth(self):
        auth = self.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), AGENT_KEY)

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True})
        if path.rstrip("/") == "/v1/models":
            if not self._auth():
                return self._send(401, {"error": {"message": "unauthorized"}})
            return self._send(200, {"object": "list", "data": [
                {"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "dominic"}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        from urllib.parse import urlparse
        if urlparse(self.path).path.rstrip("/") != "/v1/chat/completions":
            return self._send(404, {"error": "not found"})
        if not self._auth():
            return self._send(401, {"error": {"message": "unauthorized"}})
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            return self._send(413, {"error": {"message": "body too large"}})
        try:
            req = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            return self._send(400, {"error": {"message": "bad json"}})
        msgs = req.get("messages", [])
        if (not req.get("stream") and req.get("max_tokens") == 1024 and msgs
                and str(msgs[-1].get("content", "")).strip().lower() == "hi"):
            return self._send(200, completion("chatcmpl-probe", "Halo! Dominic Spark online dan siap."))
        answer = None
        holder = {}
        done = threading.Event()

        def work():
            try:
                holder["a"] = agent_answer(msgs, req.get("max_tokens") or 2048)
            except Exception as e:
                holder["a"] = "Dominic Spark: backend error (" + str(e)[:200] + ")"
            done.set()

        if req.get("stream"):
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                return
            threading.Thread(target=work, daemon=True).start()
            while not done.wait(15):
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, socket.timeout):
                    return
            answer = holder.get("a", "Dominic Spark: no answer")
            cid = "chatcmpl-" + uuid.uuid4().hex[:12]
            try:
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                         "model": MODEL_ID, "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": answer},
                     "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            return
        answer = agent_answer(msgs, req.get("max_tokens") or 2048)
        self._send(200, completion("chatcmpl-" + uuid.uuid4().hex[:12], answer))


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    log(f"Dominic Spark listening on 127.0.0.1:{PORT} (backend {BACKEND_MODEL})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
