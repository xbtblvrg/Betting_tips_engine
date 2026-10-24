#!/usr/bin/env python3
import subprocess
import time
import json
import os
import sys

LOG_FILE = "logs/autobet_execution.log"

def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_safari_autobet_status():
    js_code = """
    JSON.stringify({
        desc: document.getElementById("auto-bet-desc") ? document.getElementById("auto-bet-desc").innerText : "",
        stats: document.getElementById("auto-bet-stats") ? document.getElementById("auto-bet-stats").innerText : "",
        title: document.getElementById("auto-bet-title") ? document.getElementById("auto-bet-title").innerText : "",
        card: document.getElementById("skipped-bets-card") ? document.getElementById("skipped-bets-card").innerText : ""
    })
    """
    ascript = f'tell application "Safari" to do JavaScript "{js_code.replace(chr(10), " ").replace(chr(34), chr(92)+chr(34))}" in tab 1 of window 1'
    try:
        res = subprocess.run(["osascript", "-e", ascript], capture_output=True, text=True, timeout=8)
        raw = res.stdout.strip()
        if raw and raw.startswith("{"):
            return json.loads(raw)
        elif res.stderr:
            return {"error": res.stderr.strip()}
    except Exception as e:
        return {"error": str(e)}
    return {}

def main():
    log("=== Auto-Bet Monitor Started ===")
    last_desc = ""

    while True:
        status = get_safari_autobet_status()
        desc = status.get("desc", "")
        title = status.get("title", "")
        card = status.get("card", "")

        if desc and desc != last_desc:
            log(desc)
            last_desc = desc

            # Check if auto-bet finished
            if "befejeződött" in desc.lower() or "kész" in desc.lower() or "leállítva" in desc.lower():
                log(f"AUTO-BET FINISHED / STOPPED: {desc}")
                if card:
                    log(f"Skipped card content:\n{card}")
                break

        # Check if title indicates stopped
        if title and ("megállt" in title.lower() or "befejeződött" in title.lower()):
            log(f"PROCESS ENDED: {title} | {desc}")
            if card:
                log(f"Skipped card content:\n{card}")
            break

        time.sleep(3)

if __name__ == "__main__":
    main()
