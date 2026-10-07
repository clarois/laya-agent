#!/usr/bin/env python
# Shared primitives every other module imports: logging, goal loading, the game
# API door, and distance. Keeping these here avoids circular imports when
# combat.py / game_api.py / knight_build.py all need the same pieces.
import json, os, time, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "log.txt")
GOAL_FILE = os.path.join(BASE, "goal.json")

def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_goal():
    try:
        with open(GOAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# --- game API ---
CONN = json.load(open(os.path.expandvars(r"%APPDATA%\Ragnarok\Offline\state\agent\connection.json").replace("Ragnarok\\Offline", "Ragnarok Offline"), encoding="utf-8"))
PORT, TOKEN = CONN["port"], CONN["token"]

def api(cmd, args=None):
    body = json.dumps({"cmd": cmd, "args": args or []}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/command" % PORT, data=body, method="POST",
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}

def dist(a, b):
    return max(abs(a[0]-b[0]), abs(a[1]-b[1]))
