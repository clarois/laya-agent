#!/usr/bin/env python
# Death recovery: screenshot the client, find the "Return to last save point"
# button by pixel analysis, click it. Needs PIL (in the laya venv).
import time
from core import api, log

def find_death_button(shot_path):
    """Locate the 'Return to last save point' button in a screenshot by pixel analysis.
    Returns (x, y) in screenshot coordinates, or None."""
    from PIL import Image
    im = Image.open(shot_path).convert('RGB')
    W, H = im.size
    px = im.load()
    rows = []
    for y in range(0, H):
        cnt, x0, x1 = 0, None, None
        for x in range(0, W):
            r, g, b = px[x, y]
            if r > 235 and g > 235 and b > 235:
                if x0 is None:
                    x0 = x
                x1 = x
                cnt += 1
        if cnt > 150 and (x1 - x0) > 200:
            rows.append((y, x0, x1, cnt))
    if not rows:
        return None
    ys = [r[0] for r in rows]
    blocks = []
    start = prev = ys[0]
    for y in ys[1:]:
        if y - prev > 6:
            blocks.append((start, prev)); start = y
        prev = y
    blocks.append((start, prev))
    best = max(blocks, key=lambda b: b[1] - b[0])
    band = [r for r in rows if best[0] <= r[0] <= best[1]]
    x0 = min(r[1] for r in band); x1 = max(r[2] for r in band)
    if x1 - x0 < 200:
        return None
    cx = (x0 + x1) // 2
    for y in range(best[0], best[1]):
        dark = [x for x in range(x0, x1) if sum(px[x, y]) < 300]
        if len(dark) > 10:
            run_start = y
            y2 = y
            while y2 < best[1]:
                d2 = [x for x in range(x0, x1) if sum(px[x, y2]) < 300]
                if len(d2) <= 5:
                    break
                y2 += 1
            return (cx, (run_start + y2) // 2)
    return None

def recover_death():
    r = api("shot", ["death_recovery"])
    f = r.get("file")
    if not f:
        log("death recovery: no screenshot available (%s)" % r)
        return False
    pos = find_death_button(f)
    if not pos:
        log("death recovery: no dialog found in screenshot")
        return False
    log("death recovery: clicking %s,%s" % pos)
    api("click", [str(pos[0]), str(pos[1])])
    time.sleep(3)
    st = api("state", ["2"])
    p = st.get("player") or {}
    return (not p.get("dead", True)) if p else False
