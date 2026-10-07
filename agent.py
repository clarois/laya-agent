#!/usr/bin/env python
# Laya-driven Ragnarok agent: brain (Hermes) sets goal.json, Laya picks actions,
# this script orchestrates. The heavy lifting lives in sibling modules:
#   core.py          shared primitives (log, api, load_goal, dist)
#   combat.py        execute(): attack/flee/wander/heal/greet/allocate
#   game_api.py      death-recovery (screenshot -> click respawn)
#   knight_build.py  stat read + Knight build plan + drain_stat (spend points)
#   dbread.py        live DB snapshot (char, inventory, equipment, mobs) for Laya's state
# Usage: python agent.py [--steps N] [--interval S]   (no --steps = run forever)
import json, sys, time

from core import log, load_goal, api, dist
import dbread
import knight_build
from combat import execute
from game_api import recover_death

# --- Laya policy ---
import laya
policy = laya.load("convaiinnovations/laya", subfolder="typed-decisions")

ACTION_DEFS = {
    "attack": "a monster is nearby and it is safe to fight it",
    "flee": "danger is high: move away from the nearest monster",
    "heal": "hp is low: use a potion or rest",
    "wander": "walk to a nearby cell and explore calmly",
    "greet": "say one short friendly line in local chat",
    "idle": "stand still and watch chat",
    "allocate": "spend available stat points into the next build stat (only when enough points are banked)",
}

def build_state(raw, goal):
    p = raw.get("player", {})
    pos = p.get("position", [0, 0])
    hp, hpmax = p.get("hp", {}).get("hp", 1), p.get("hp", {}).get("max", 1) or 1
    sp, spmax = p.get("sp", {}).get("sp", 0), p.get("sp", {}).get("max", 1) or 1
    ents = raw.get("entities", [])
    # mobs: live HP when known (engaged target), static mob_db stats for everything else
    mobs = []
    for e in ents:
        if e.get("type") != "MOB" or e.get("dead"):
            continue
        ehp = e.get("hp") or {}
        live_hp, live_max = ehp.get("hp", -1), ehp.get("max", -1)
        st_ = dbread.mob_stats(e.get("job")) or dbread.mob_stats(e.get("name")) or {}
        xy = e.get("position", pos)
        m = {"name": e.get("name", "?"), "dist": round(dist(pos, xy), 1),
             "xy": [round(xy[0]), round(xy[1])],
             "lv": st_.get("level"), "maxhp": (live_max if live_max != -1 else st_.get("maxhp")),
             "atk": st_.get("atk"), "base_exp": st_.get("base_exp"),
             "elem": st_.get("elem"), "race": st_.get("race")}
        if live_hp != -1:
            m["hp"] = live_hp  # only present when this is our engaged target
        mobs.append(m)
    mobs.sort(key=lambda m: m["dist"])
    players = [e for e in ents if e.get("type") == "PC" and e.get("gid") != p.get("gid")]
    npcs = [e for e in ents if e.get("type") == "NPC"]
    chat = [c.get("text", "") for c in (raw.get("chat") or [])
            if not c.get("text", "").startswith("@@")
            and "Autoloot" not in c.get("text", "")][-4:]
    # live DB snapshot (char stats, named inventory, equipment) - throttled inside dbread
    snap = dbread.live(ttl=4)
    ch = snap.get("char") or {}
    inv = [(i["name"][:24], i["amount"]) for i in (snap.get("inventory") or [])][:20]
    eq = [g["name"][:24] for g in (snap.get("equipment") or [])][:10]
    skills = skills_cached()
    return {
        "map": (p.get("map") or "?").replace(".gat", ""),
        "pos": pos, "hp_pct": round(100*hp/hpmax), "sp_pct": round(100*sp/spmax),
        "dead": p.get("dead", False),
        "me": {"lv": ch.get("base_level"), "job_lv": ch.get("job_level"),
               "zeny": ch.get("zeny"), "stats": ch.get("stats"),
               "skill_points": ch.get("skill_point"), "status_points": ch.get("status_point")},
        "items": inv, "equipment": eq, "skills": skills,
        # Knight build: which stat to raise next and whether the bank covers it.
        "knight_next": knight_build._knight_next(ch),
        "alloc_cost": knight_build._alloc_cost(ch),
        "monsters": mobs[:6],
        "players_near": len(players), "npcs_near": len(npcs),
        "chat_recent": chat, "goal": goal.get("goal", ""),
        "rules": goal.get("rules", {}),
    }

_skills_cache = {"t": 0.0, "data": None}
def skills_cached(ttl=4.0):
    now = time.time()
    if _skills_cache["data"] is None or now - _skills_cache["t"] > ttl:
        r = api("skills")
        if isinstance(r, list):
            _skills_cache["data"] = [{"name": s.get("name"), "lv": s.get("level")}
                                     for s in r][:12]
        _skills_cache["t"] = now
    return _skills_cache["data"] or []

def allowed_actions(st, goal):
    rules = goal.get("rules", {})
    acts = ["wander", "idle"]
    if st["monsters"] and rules.get("attack", False):
        acts = ["attack", "flee"] + acts
    if (st["hp_pct"] < 40) or (rules.get("heal", False) and st["hp_pct"] < 60):
        if "heal" not in acts:
            acts.append("heal")
    if rules.get("greet", False) and (st["players_near"] or st["chat_recent"]):
        acts.append("greet")
    return acts

def step(goal):
    raw = api("state", ["40"])
    if not raw.get("player"):
        log("ERROR no state (not in game?)")
        return False
    st = build_state(raw, goal)
    if st["dead"]:
        log("DEAD -> recover via dialog")
        ok = recover_death()
        log("death recovery -> %s" % ok)
        time.sleep(2)
        return True
    acts = allowed_actions(st, goal)
    if st["hp_pct"] < 35 and "attack" in acts:
        acts = [a for a in acts if a != "attack"] or ["idle"]
    qs = {
        "action": {"type": "choice",
                   "instructions": "You control a game character. What should it do next?",
                   "criteria": {a: ACTION_DEFS[a] for a in acts}},
        "threat": {"type": "score",
                   "instructions": "How dangerous is the situation right now?",
                   "criteria": ["totally safe", "mild risk", "real threat", "about to die"]},
    }
    # Laya also checks the stat bank: a yes/no on whether points are there to spend.
    if goal.get("rules", {}).get("build", True) and st.get("knight_next") and st["hp_pct"] > 35:
        qs["should_build"] = {
            "type": "noul",
            "instructions": ("Check status_points under 'me'. Are there enough stat points "
                             "banked to spend?"),
            "criteria": {"false": "status_points is 0 — nothing to spend",
                         "true": "status_points is above 0 — enough to spend, drain it all"}}
    r = policy.predict(json.dumps(st, ensure_ascii=False), qs)
    a = r["answers"]
    action = a["action"]["choice"]
    threat = a["threat"]["score"]
    # If Laya says points are banked, spend them (maintenance, before combat act).
    sb = a.get("should_build", {})
    if sb.get("noul", 0) >= 0.5 and st.get("knight_next") and knight_build.live_bank() > 0:
        bank_now = knight_build.live_bank()
        spent, after = knight_build.drain_stat(bank_now, st["knight_next"])
        if spent:
            log("  BUILD knight: +%d %s (bank left %d, str%d vit%d agi%d)" % (
                spent, st["knight_next"], knight_build.live_bank(),
                after.get("str", 0), after.get("vit", 0), after.get("agi", 0)))
    log("map=%s pos=%s hp=%d%% mons=%d players=%d | laya: %s (threat %.2f, conf %.2f)" % (
        st["map"], st["pos"], st["hp_pct"], len(st["monsters"]), st["players_near"],
        action, threat, a["action"].get("confidence", 0)))
    if action in acts:
        log("  -> %s" % execute(action, st, raw, goal))
    else:
        log("  -> laya picked '%s' not in allowed %s; idle" % (action, acts))
    # chat check: whispers/local naming Hermes get logged loud
    for c in raw.get("chat") or []:
        if c.get("channel") in ("whisper", "party") or "hermes" in (c.get("text", "")+str(c.get("from", ""))).lower():
            log("  CHAT-FOR-US [%s] %s: %s" % (c.get("channel"), c.get("from"), c.get("text")))
    return True

def main():
    steps = None
    interval = 4.0
    argv = sys.argv[1:]
    if "--steps" in argv:
        steps = int(argv[argv.index("--steps")+1])
    if "--interval" in argv:
        interval = float(argv[argv.index("--interval")+1])
    log("=== laya-agent start (steps=%s interval=%s)" % (steps, interval))
    goal = load_goal()
    log("goal: %s | rules: %s" % (goal.get("goal"), goal.get("rules")))
    n = 0
    try:
        while steps is None or n < steps:
            goal = load_goal()  # brain can update goal mid-run
            ok = step(goal)
            n += 1
            if steps is not None and n >= steps:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        log("stopped by user")
    log("=== laya-agent end after %d steps" % n)

if __name__ == "__main__":
    main()
