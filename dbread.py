# dbread.py — live game DB + static item/mob data for the Laya state.
# Reads via ragnarok-stack.exe (no docker needed): `sql` for live rows, `export-table` for static YAML.
import json, os, re, subprocess, time

STACK = os.path.expandvars(r"%APPDATA%\Ragnarok Offline\runtime\bin\ragnarok-stack.exe")
BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "static_cache.json")
CHAR_ID = 150003  # Hermes (aiagent)

def sql(query, timeout=15):
    """Run a read-only SQL, return list of dict rows ([] on error)."""
    try:
        r = subprocess.run([STACK, "sql", query], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return []
    out = r.stdout or ""
    if "ERROR" in out or "not a table" in out:
        return []
    rows, header = [], None
    for line in out.splitlines():
        line = line.rstrip("\r")
        if not line.strip() or line.startswith("----"):
            continue
        if header is None:
            if "\t" in line:
                header = line.split("\t")
            elif re.fullmatch(r"[a-z_0-9]+", line):   # single-column header
                header = [line]
            continue  # query echo / dashes ignored
        vals = line.split("\t")
        rows.append(dict(zip(header, vals)))
    return rows

def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v

def char_stats():
    rows = sql("SELECT base_level, job_level, base_exp, job_exp, class, zeny, hp, max_hp, "
               "sp, max_sp, str, agi, vit, `int`, dex, luk, status_point, skill_point "
               "FROM `char` WHERE char_id=%d" % CHAR_ID)
    if not rows:
        return None
    c = rows[0]
    return {
        "base_level": _to_int(c["base_level"]), "job_level": _to_int(c["job_level"]),
        "base_exp": _to_int(c["base_exp"]), "job_exp": _to_int(c["job_exp"]),
        "job_class": _to_int(c["class"]), "zeny": _to_int(c["zeny"]),
        "hp": _to_int(c["hp"]), "max_hp": _to_int(c["max_hp"]),
        "sp": _to_int(c["sp"]), "max_sp": _to_int(c["max_sp"]),
        "stats": {"str": _to_int(c["str"]), "agi": _to_int(c["agi"]), "vit": _to_int(c["vit"]),
                  "int": _to_int(c["int"]), "dex": _to_int(c["dex"]), "luk": _to_int(c["luk"])},
        "status_point": _to_int(c["status_point"]), "skill_point": _to_int(c["skill_point"]),
    }

def inventory():
    """Rows merged with static item names. Returns (items, equipment)."""
    rows = sql("SELECT nameid, amount, equip, refine FROM inventory WHERE char_id=%d" % CHAR_ID)
    static = static_data()
    items, equipped = [], []
    for r in rows:
        nameid = _to_int(r["nameid"])
        meta = static["items"].get(str(nameid), {})
        entry = {"name": (meta.get("name") or ("item_%s" % nameid)).strip('"'),
                 "id": nameid, "amount": _to_int(r["amount"]),
                 "refine": _to_int(r["refine"])}
        mask = _to_int(r["equip"])
        if mask:
            entry["slot_mask"] = mask
            equipped.append({"name": entry["name"], "amount": entry["amount"], "refine": entry["refine"]})
        else:
            items.append(entry)
    return items, equipped

def mob_stats(name):
    """Static stats for a mob, keyed by sprite id (exact) or unambiguous display name."""
    sd = static_data()
    return sd["mobs"].get(str(name)) or sd.get("mobs_name", {}).get(name)

def _parse_yaml_entries(yaml_text):
    """Generic rAthena YAML entry parser: only direct-child keys (indent 4)."""
    entries, cur = [], None
    for line in yaml_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^  - Id: (\d+)", line)
        if m:
            if cur:
                entries.append(cur)
            cur = {"id": int(m.group(1))}
            continue
        if cur is None:
            continue
        m = re.match(r"^    ([A-Za-z0-9_]+): (.*)$", line)
        if m:
            cur[m.group(1)] = m.group(2).strip()
    if cur:
        entries.append(cur)
    return entries

def build_static(force=False):
    """One-time (re)build of item/mob name+stat maps from export-table."""
    if not force and os.path.exists(CACHE) and (time.time() - os.path.getmtime(CACHE)) < 7 * 86400:
        return json.load(open(CACHE, encoding="utf-8"))
    data = {"items": {}, "mobs": {}}
    for tbl in ("item_db_equip", "item_db_etc", "item_db_usable"):
        try:
            out = subprocess.run([STACK, "export-table", tbl], capture_output=True,
                                 text=True, timeout=60).stdout
        except Exception:
            continue
        for e in _parse_yaml_entries(out):
            name = e.get("Name") or e.get("AegisName")
            if name:
                data["items"][str(e["id"])] = {"name": name, "type": e.get("Type", "?")}
    try:
        out = subprocess.run([STACK, "export-table", "mob_db"], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception:
        out = ""
    for e in _parse_yaml_entries(out):
        name = e.get("Name") or e.get("AegisName")
        if not name:
            continue
        data["mobs"][str(e["id"])] = {
            "name": name, "aegis": e.get("AegisName"),
            "level": _to_int(e.get("Level")), "maxhp": _to_int(e.get("Hp")),
            "atk": _to_int(e.get("Attack")), "def": _to_int(e.get("Defense")),
            "mdef": _to_int(e.get("MagicDefense")),
            "elem": (e.get("Element", "?") + str(_to_int(e.get("ElementLevel")) or "")),
            "size": e.get("Size", "?"), "race": e.get("Race", "?"),
            "base_exp": _to_int(e.get("BaseExp")), "job_exp": _to_int(e.get("JobExp")),
            "atk_range": _to_int(e.get("AttackRange")),
        }
    # unambiguous display-name fallback (503 names collide across variants: Fabre x6 etc.)
    namecount = {}
    for mid, m in data["mobs"].items():
        namecount[m["name"]] = namecount.get(m["name"], 0) + 1
    data["mobs_name"] = {m["name"]: m for mid, m in data["mobs"].items()
                         if namecount[m["name"]] == 1}
    json.dump(data, open(CACHE, "w", encoding="utf-8"))
    return data

_static = None
def static_data():
    global _static
    if _static is None:
        _static = build_static()
    return _static

_live_cache = {"t": 0.0, "data": None}
def live(ttl=4.0):
    """Throttled live snapshot: {char, inventory, equipment}."""
    now = time.time()
    if _live_cache["data"] is None or now - _live_cache["t"] > ttl:
        ch = char_stats()
        inv, eq = inventory()
        _live_cache["data"] = {"char": ch, "inventory": inv, "equipment": eq}
        _live_cache["t"] = now
    return _live_cache["data"]

if __name__ == "__main__":
    import time as _t
    t0 = _t.time()
    sd = build_static(force=True)
    print("static: %d items, %d mobs (%.1fs)" % (len(sd["items"]), len(sd["mobs"]), _t.time() - t0))
    t0 = _t.time()
    snap = live(ttl=0)
    print("live (%.2fs):" % (_t.time() - t0))
    print(json.dumps(snap, indent=1)[:900])
    print("mob sample Pupa:", sd["mobs"].get("Pupa"))
    print("item sample 1201:", sd["items"].get("1201"))
