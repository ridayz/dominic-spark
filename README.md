# Dominic Spark

Mini AI agent as an OpenAI-compatible endpoint (Python stdlib only, ~50 MB RAM).

- `GET /health` → `{"ok": true}`
- `GET /v1/models` → lists `dominic-spark` (needs bearer key)
- `POST /v1/chat/completions` → ReAct loop: backend LLM + local tools
  (`read_file`, `write_file`, `run_shell` jailed in `DOM_WORKDIR`), streaming supported.

## Run

```powershell
python agent.py   # serves 127.0.0.1:8766, generates key.txt on first run
```

Or hidden with auto-restart:

```powershell
pythonw run-agent.py
```

## Config (env)

| Var | Default |
|---|---|
| `DOM_PORT` | `8766` |
| `DOM_WORKDIR` | project folder |
| `DOM_BACKEND_URL` | `http://127.0.0.1:20128/v1` (9Router) |
| `DOM_BACKEND_MODEL` | `agnes/agnes-3.0-flash` |
| Backend key | read from 9Router `Default Key` in its sqlite DB |

## Register in 9Router

Via dashboard API (authenticated session):

1. `POST /api/provider-nodes` `{type:'openai-compatible', name:'Dominic Spark', prefix:'dom', apiType:'chat', baseUrl:'http://127.0.0.1:8766/v1'}` → note node id (`openai-compatible-chat-<uuid>`)
2. `POST /api/providers` `{provider:<node-id>, name:'Dominic Key', apiKey:<key.txt>, priority:1, ...}` → connection id
3. `POST /api/models/custom` `{providerAlias:'dom', id:'dominic-spark', type:'llm', name:'dominic-spark'}`
4. Use as `dom/dominic-spark`. Test with `{"messages":[{"role":"user","content":"hi"}],"max_tokens":1024}` (instant probe reply, no tools).

`key.txt` is generated locally and never committed.
