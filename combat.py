#!/usr/bin/env python
# Combat/survival actions Laya picks: attack, flee, wander, heal, greet.
# execute(action, st, raw, goal) -> str (a log line describing what happened).
import random, time
from core import api, dist
import dbread

GREET_COOLDOWN = 90.0
last_greet = 0.0

def execute(action, st, raw, goal):
    global last_greet
    import knight_build
    pos = st["pos"]
    if action == "attack":
        r = api("attack", ["nearest"])
        return "attack nearest -> %s" % (not r.get("error"))
    if action == "flee":
        if st["monsters"]:
            m = st["monsters"][0]
            # walk away: direction from monster raw pos unknown; use raw entities
            mon = next((e for e in raw.get("entities", [])
                        if e.get("type") == "MOB" and e.get("name") == m["name"]), None)
            if mon:
                mp = mon["position"]
                dx, dy = pos[0]-mp[0], pos[1]-mp[1]
                n = max(abs(dx), abs(dy)) or 1
                warps = [e["position"] for e in raw.get("entities", []) if e.get("type") == "WARP"]
                tx, ty = pos[0], pos[1]
                for _ in range(6):
                    tx = int(pos[0]+dx/n*8)
                    ty = int(pos[1]+dy/n*8)
                    if all(dist([tx, ty], w) > 3 for w in warps):
                        break
                    tx += random.choice([-2, 2])
                    ty += random.choice([-2, 2])
                r = api("walk", [str(tx), str(ty)])
                return "flee to %d,%d -> %s" % (tx, ty, (not r.get("error")))
        return "flee: no monster"
    if action == "wander":
        warps = [e["position"] for e in raw.get("entities", []) if e.get("type") == "WARP"]
        tx, ty = pos[0], pos[1]
        for _ in range(6):  # avoid picking a cell next to a warp (it changes maps)
            tx = pos[0]+random.randint(-8, 8)
            ty = pos[1]+random.randint(-8, 8)
            if all(dist([tx, ty], w) > 3 for w in warps):
                break
        r = api("walk", [str(tx), str(ty)])
        return "wander to %d,%d -> %s" % (tx, ty, (not r.get("error")))
    if action == "heal":
        snap = dbread.live(ttl=0)  # fresh: HP just changed
        static_items = dbread.static_data()["items"]
        sp_ok = st.get("sp_pct", 100) >= 40
        # Prefer real HP potions, then milk/first aid; Blue Potion is SP, keep
        # it only when SP is low too.
        order = ["Fresh Milk", "Red Potion", "White Potion", "Yellow Potion", "Orange Potion",
                 "Herb", "First aid", "Blue Potion"]
        cands = []
        for it in snap.get("inventory") or []:
            typ = static_items.get(str(it.get("id")), {}).get("type", "")
            nm = it.get("name", "")
            if "Blue Potion" in nm and sp_ok:
                continue
            if typ == "Healing" or "aid" in nm.lower():
                rank = next((i for i, key in enumerate(order) if key.lower() in nm.lower()), len(order))
                cands.append((rank, nm, it.get("id")))
        if not cands:
            return "heal: nothing drinkable in the inventory"
        cands.sort()
        _, name, item_id = cands[0]
        r = api("use", [str(item_id)])
        err = str(r.get("error", "")) if isinstance(r, dict) else ""
        if "unknown command" in err:
            return "heal: use command not in this app build yet"
        if err:
            return "heal: use %s failed: %s" % (name, err[:80])
        return "heal: used %s" % name
    if action == "allocate":
        # Laya chose to spend points: drain the bank into the Knight priority stat
        # until it can't afford another point (or hits the 10-per-tick ceiling).
        nxt = st.get("knight_next")
        if not nxt:
            return "allocate: no deficit stat"
        want = st.get("alloc_cost", 0)
        spent, after = knight_build.drain_stat(want, nxt)
        pts = after.get("status_point", "?")
        return "allocate: +%d %s (bank now %s, str%d vit%d agi%d)" % (
            spent, nxt, pts, after.get("str", "?"), after.get("vit", "?"), after.get("agi", "?"))
    if action == "greet":
        if time.time()-last_greet < GREET_COOLDOWN:
            return "greet: cooldown"
        lines = goal.get("greetings") or ["o/", "hello all", "hi"]
        line = random.choice(lines)
        last_greet = time.time()
        r = api("say", [line])
        return "greet '%s' -> %s" % (line, (not r.get("error")))
    return "idle"
