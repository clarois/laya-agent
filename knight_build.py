# knight_build.py — read stats, assign status points toward a Knight build.
# Knight = STR (damage) / VIT (HP) / AGI (aspd). Allocation is a deterministic
# priority walk, not an LLM call: the brain (Hermes) sets the build once, the
# script spends points every tick toward the nearest deficit.
import json, os, subprocess, time

STACK = os.path.expandvars(r"%APPDATA%\Ragnarok Offline\runtime\bin\ragnarok-stack.exe")
CHAR_ID = 150003  # Hermes

# Knight stat priority (in order of target ratios). Each entry: stat -> target value
# at the current base level. These are NORMALISED targets, scaled by level so a
# level-1 novice and a level-99 knight both sit on the curve.
#   STR 99 build (SVD knight): STR leads, VIT close, AGI for aspd, DEX filler.
KNIGHT_TARGET = {
    "str": 1.00,   # primary — weapon damage
    "vit": 0.90,   # secondary — HP pool / survivability
    "agi": 0.70,   # tertiary — attack speed / flee
    "dex": 0.40,   # hit chance filler (affects nothing here but keeps parity)
    "int": 0.00,   # knight ignores int
    "luk": 0.00,   # knight ignores luk
}
# When to stop spending (per-stat caps) so we never over-invest past the build.
KNIGHT_CAP = {"str": 99, "vit": 99, "agi": 99, "dex": 99, "int": 1, "luk": 1}


def sql(query, timeout=15):
    try:
        r = subprocess.run([STACK, "sql", query], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return []
    out = r.stdout or ""
    if "ERROR" in out or "Unknown column" in out:
        return []
    rows, header = [], None
    for line in out.splitlines():
        line = line.rstrip("\r")
        if not line.strip() or line.startswith("----"):
            continue
        if header is None:
            if "\t" in line:
                header = line.split("\t")
            elif line.isidentifier():
                header = [line]
            continue
        vals = line.split("\t")
        rows.append(dict(zip(header, vals)))
    return rows


def read_stats():
    """Current stats + points from the char table (server-side source of truth)."""
    rows = sql(
        "SELECT base_level, status_point, str, agi, vit, `int`, dex, luk "
        "FROM `char` WHERE char_id=%d" % CHAR_ID
    )
    if not rows:
        return None
    r = rows[0]
    def n(k):
        try:
            return int(r[k])
        except (KeyError, TypeError, ValueError):
            return 0
    return {"base_level": n("base_level"), "status_point": n("status_point"),
            "str": n("str"), "agi": n("agi"), "vit": n("vit"), "int": n("int"),
            "dex": n("dex"), "luk": n("luk")}


def _target_for(stat, base_level):
    """Scaled target: build ratio * a per-level point budget, capped per stat."""
    budget = 2 + int(base_level * 2)  # rough points-to-spend-per-stat at this level
    return min(KNIGHT_CAP[stat], round(KNIGHT_TARGET[stat] * budget))


def plan(stats, top=1):
    """Which stat(s) to raise next, in priority order. Returns list of stat names."""
    if not stats:
        return []
    level = stats["base_level"]
    deficits = []
    for stat in ("str", "vit", "agi", "dex", "int", "luk"):
        want = _target_for(stat, level)
        have = stats[stat]
        if have < want:
            deficits.append((want - have, stat))
    # biggest deficit first, but keep priority order (str > vit > agi) as tiebreak
    prio = {"str": 0, "vit": 1, "agi": 2, "dex": 3, "int": 4, "luk": 5}
    deficits.sort(key=lambda d: (-d[0], prio[d[1]]))
    return [s for _, s in deficits[:top]]


def assign(stat, n=1):
    """The chat command that spends n status points into one stat (client /<stat>+ N)."""
    return "/%s+ %d" % (stat, n)


def _knight_next(ch):
    """The next stat in the Knight build priority (STR>VIT>AGI), or None if capped."""
    lvl = ch.get("base_level", 1) or 1
    for stat in ("str", "vit", "agi", "dex", "int", "luk"):
        have = ch.get("stats", {}).get(stat, 0) or 0
        if have < _target_for(stat, lvl):
            return stat
    return None


def _alloc_cost(ch):
    """Status points to spend this tick: the whole bank (Laya drains until it can't)."""
    return ch.get("status_point", 0) or 0


# In-memory point bank: the DB lags minutes behind, so we track what we've spent
# ourselves. Seeded from the DB on first use, then decremented by every send.
_bank = {"seeded": False, "left": 0}
def live_bank():
    if not _bank["seeded"]:
        s = read_stats() or {}
        _bank["left"] = s.get("status_point", 0) or 0
        _bank["seeded"] = True
    return _bank["left"]


def drain_stat(n, first_stat):
    """Spend up to n status points following the full Knight priority (STR>VIT>AGI).

    The server accepts each /<stat>+ N and the client applies it immediately; the
    DB write lags seconds-to-minutes, so we trust the command (proven reliable)
    rather than polling the stale DB. Sends in paced batches of up to 10 until the
    budget n is spent or the plan runs out of deficits. Returns (sent, last_known)."""
    from core import api
    sent = 0
    cur = read_stats() or {}
    budget = min(n, live_bank())  # never spend more than we actually have left
    while sent < budget:
        # re-plan off the last-known stats (client applies as we go)
        nxt = plan(cur, top=1)
        if not nxt:
            break
        stat = nxt[0]
        lvl = cur.get("base_level", 1) or 1
        deficit = max(0, _target_for(stat, lvl) - (cur.get(stat, 0) or 0))
        if deficit <= 0:
            break
        bank = cur.get("status_point", 0) or 0
        batch = min(10, bank, deficit, n - sent)
        if batch <= 0:
            break
        api("say", ["/%s+ %d" % (stat, batch)])
        # optimistically apply locally so the next plan sees the higher stat
        cur["status_point"] = bank - batch
        cur[stat] = (cur.get(stat, 0) or 0) + batch
        _bank["left"] = max(0, live_bank() - batch)
        sent += batch
        time.sleep(0.4)  # pace sends so the server isn't spammed (anti-cheat)
    # confirm against fresh DB read after a settle wait (best effort, for logging)
    time.sleep(1.5)
    after = read_stats() or cur
    return sent, after


if __name__ == "__main__":
    s = read_stats()
    print("stats:", json.dumps(s))
    if s:
        print("next (knight):", plan(s, 3))
        print("targets at lv%s:" % s["base_level"],
              {k: _target_for(k, s["base_level"]) for k in KNIGHT_TARGET})
