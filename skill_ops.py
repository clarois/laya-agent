#!/usr/bin/env python
# Skill-point operations: read the skill tree, spend points toward a build preset.
# Presets live in skill_db.json (built from the client's SkillInfo/SkillConst).
# Spend = send CZ.UPGRADE_SKILLLEVEL once per point (same packet the skill
# window's "+" uses), so the server validates prerequisites and refunds nothing.
import json, os, time

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL_DB = os.path.join(BASE, "skill_db.json")

def _db():
    try:
        return json.load(open(SKILL_DB, encoding="utf-8"))
    except Exception:
        return {"presets": {}, "skills": {}}

def preset(name):
    return _db()["presets"].get(name, [])

def read_skills(api):
    """Current skill levels from the game (id -> level). Uses the API's skills cmd."""
    r = api("skills")
    out = {}
    if isinstance(r, list):
        for s in r:
            lv = s.get("level")
            if lv:  # level 0 / null = not learned
                out[s.get("id")] = lv
    return out

def skill_points(api):
    """Pending skill points. From the char table (server truth) — cheap SQL."""
    import knight_build
    rows = knight_build.sql("SELECT skill_point FROM `char` WHERE char_id=%d" % knight_build.CHAR_ID)
    try:
        return int(rows[0]["skill_point"])
    except Exception:
        return 0

def _prereq_ok(need, owned, plan):
    """Check a skill's prerequisites against already-owned + planned levels."""
    for need_key, need_lvl in need:
        have = owned.get(_id_of(need_key), 0)
        # plan levels are stored by skill id; need_key -> resolve
        planned = plan.get(_id_of(need_key), 0)
        if max(have, planned) < need_lvl:
            return False
    return True

_id_cache = {}
def _id_of(key):
    if key in _id_cache:
        return _id_cache[key]
    db = _db()["skills"]
    sid = db.get(key, {}).get("id")
    _id_cache[key] = sid
    return sid

def plan_preset(name, api):
    """Ordered list of (skill_id, remaining_upgrades) to reach the preset, given
    current skills and pending points. Respects prerequisites via ordered walk."""
    db = _db()
    skills = db["presets"].get(name, [])
    owned = read_skills(api)
    points = skill_points(api)
    order = []
    # The preset is authored in a prereq-safe order; walk it, stopping when points run out.
    planned = dict(owned)  # id -> level, as we go
    spent = 0
    for e in skills:
        if spent >= points:
            break
        sid, want, key = e["id"], e["level"], e["key"]
        have = planned.get(sid, 0)
        if have >= want:
            continue
        need = db["skills"].get(key, {}).get("need", [])
        if not _prereq_ok(need, planned, {}):
            continue  # can't reach it yet; skip (server would refuse)
        ups = min(want - have, points - spent)
        if ups <= 0:
            continue
        order.append((sid, ups, e["name"]))
        planned[sid] = have + ups
        spent += ups
    return order, points, spent

def spend(api, preset_name, log=print):
    """Spend pending skill points toward a preset. Returns (learned_total, msg)."""
    order, points, planned = plan_preset(preset_name, api)
    if not order:
        return 0, "skills: nothing to learn (no points, or preset reached)"
    learned = 0
    for sid, ups, name in order:
        r = api("learn", [str(sid), str(ups)])
        err = str(r.get("error", "")) if isinstance(r, dict) else ""
        if "unknown command" in err:
            return learned, "skills: learn command not in this app build yet"
        if err:
            return learned, "skills: learn %s failed: %s" % (name, err[:80])
        learned += ups
        time.sleep(0.3)
        log("  SKILL %s +%d (id %d)" % (name, ups, sid))
    return learned, "skills: spent %d of %d points on %s" % (learned, points, preset_name)
