import os
import subprocess
import sys
import time
BASE = r"C:\dominic-spark"
LOG = os.path.join(BASE, "agent.log")


def main():
    with open(LOG, "a") as log:
        log.write("--- supervisor start ---\n")
        log.flush()
        while True:
            p = subprocess.Popen([sys.executable, os.path.join(BASE, "agent.py")],
                                 stdout=log, stderr=subprocess.STDOUT)
            p.wait()
            log.write(f"agent exited ({p.returncode}), restart in 5s\n")
            log.flush()
            time.sleep(5)


if __name__ == "__main__":
    main()
