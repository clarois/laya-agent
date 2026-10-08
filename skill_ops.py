#!/usr/bin/env python
# Skill-point operations: read the skill tree, spend points toward a build preset.
# Presets live in skill_db.json (built from the client's SkillInfo/SkillConst).
# Spend = send CZ.UPGRADE_SKILLLEVEL once per point (same packet the skill
# window's "+" uses), so the server validates prerequisites and refunds nothing.
import json, os, time
import js_ops

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
    """Current skill levels (id -> level). Read from the client via `js`
    (window.roAgent.skills() — live, no DB lag). Falls back to the skills cmd."""
    r = js_ops.run(api, "skills")
    out = {}
    def _collect(lst):
        for s in lst or []:
            lv = s.get("level")
            sid = s.get("id", s.get("skillId"))
            if sid is not None and lv:
                out[sid] = lv
    if isinstance(r, list):
        _collect(r)
    elif isinstance(r, dict) and isinstance(r.get("skills"), list):
        _collect(r["skills"])
    if out:
        return out
    # fallback: driver skills command
    r = api("skills")
    if isinstance(r, list):
        _collect(r)
    return out

def skill_points(api):
    """Pending skill points, read live from the client (SkillList DOM / Session)
    via `js` — the SQL/DB lags minutes behind. Returns 0 if unreadable."""
    body = """
        try {
            const S = window.roAgent.modules.Session;
            // Session.Entity often carries skillPoint / skill_point
            const e = S && S.Entity;
            if (e && (e.skillPoint != null || e.skill_point != null))
                return { points: e.skillPoint != null ? e.skillPoint : e.skill_point };
        } catch (err) {}
        // Fall back to the SkillList window's own "Skill Points: N" label.
        try {
            const list = window.roAgent.modules.UIManager.getComponent('SkillList');
            const root = list && list.getRoot ? list.getRoot() : null;
            if (root) {
                const txt = root.textContent || '';
                const m = txt.match(/Skill Points\\s*:?\\s*(\\d+)/i);
                if (m) return { points: parseInt(m[1], 10) };
            }
        } catch (err) {}
        return { points: null };
    """
    r = js_ops.run(api, body)
    if isinstance(r, dict) and isinstance(r.get("points"), int):
        return r["points"]
    # fallback to the char table (server truth, may lag)
    try:
        import knight_build
        rows = knight_build.sql("SELECT skill_point FROM `char` WHERE char_id=%d" % knight_build.CHAR_ID)
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
