#!/usr/bin/env python
# Street arbitrage: buy items where a sell-shop asks LESS than a buy-shop bids,
# then resell to the buyer for the spread. Laya decides WHERE to explore next
# (this dump is one side of the road; counterparties live on the other side too).
#
# Data flow:  shops.json (from dump_shops)  ->  match()  ->  Arbs[]
# Each Arb:   {item, buy_from, ask, stock, sell_to, bid, demand, spread, max_qty, gross}
#
# Matching is by NORMALIZED name (strip +9 refine, [2] slots) so "+9 Red Herb [4]"
# pairs with "Red Herb". Only pairs where ask < bid are profitable.
import json, os, re, time
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
SHOPS_JSON = os.path.join(BASE, "shops.json")


# ---------- normalization / parsing ----------

def price(s):
    """'10,500' / '10500z' -> int, else None."""
    try:
        return int(str(s).replace(",", "").replace("z", "").replace("Z", "").strip())
    except Exception:
        return None


def qty(s):
    """'19' or '132 ea.' -> int stock, else None."""
    m = re.search(r"[\d,]+", str(s) or "")
    try:
        return int(m.group(0).replace(",", "")) if m else None
    except Exception:
        return None


def norm_name(name):
    """Normalize an item name for matching: drop slot brackets ('[2]') FIRST
    (so their digits aren't eaten by the refine strip), then leading refine
    ('+9 '), collapse whitespace, lowercase."""
    n = str(name).lower()
    n = re.sub(r"\[\d+\]", "", n)        # '[2]' slots (before digit strip!)
    n = re.sub(r"[+\d]+\s*", "", n)      # '+9 ' refine prefix + stray digits
    n = re.sub(r"\s+", " ", n).strip()
    return n


# ---------- load + index ----------

def load(path=SHOPS_JSON):
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("shops", [])


def rows(shops, kind):
    """Flatten open shops of `kind` into item rows with parsed price/qty."""
    out = []
    for s in shops:
        if not s.get("open") or s.get("kind") != kind:
            continue
        for it in s.get("items", []):
            out.append({
                "name": it.get("name", ""),
                "key": norm_name(it.get("name", "")),
                "ask": price(it.get("price")) if kind == "sell" else price(it.get("price")),
                "qty": qty(it.get("amount")),
                "seller": s.get("seller"),
                "sign": s.get("sign"),
                "kind": kind,
            })
    return out


def match(shops):
    """All profitable pairs: buy side = sell-shop (ask), sell side = buy-shop (bid).
    Returns Arbs sorted by gross (spread * fillable qty) descending."""
    sells = [r for r in rows(shops, "sell") if r["ask"] is not None]
    buys = [r for r in rows(shops, "buy") if r["ask"] is not None]
    by_key = defaultdict(list)
    for s in sells:
        by_key[s["key"]].append(s)

    # Collect candidate pairs, then DEDUP: one buyer (by seller) may have many
    # signs, one seller many signs — counting each combination would double-count
    # the same buyer's demand / same seller's stock. Keep the BEST (highest gross)
    # pair per (buyer seller, item key).
    best = {}
    for b in buys:
        for s in by_key.get(b["key"], []):
            if s["ask"] < b["ask"]:
                spread = b["ask"] - s["ask"]
                fill = max(1, min(s["qty"] or 1, b["qty"] or 1))
                cand = {
                    "item": b["name"], "key": b["key"],
                    "buy_from": {"seller": s["seller"], "sign": s["sign"]},
                    "ask": s["ask"], "stock": s["qty"],
                    "sell_to": {"seller": b["seller"], "sign": b["sign"]},
                    "bid": b["ask"], "demand": b["qty"],
                    "spread": spread, "max_qty": fill, "gross": spread * fill,
                }
                dedup_key = (b["seller"], b["key"])   # one trade per buyer per item
                prev = best.get(dedup_key)
                if prev is None or cand["gross"] > prev["gross"]:
                    best[dedup_key] = cand
    arbs = sorted(best.values(), key=lambda a: -a["gross"])
    return arbs


# ---------- Laya exploration question ----------

EXPLORATION_Q = {
    "type": "choice",
    "instructions": (
        "You are scouting a Ragnarok street market for arbitrage. Current known "
        "shops are on the LEFT side of the road only. The RIGHT side (and further "
        "up/down the street) is unmapped and likely holds more buy/sell vendors. "
        "Some known pairs are thin. Choose the best next move to find more "
        "profitable counterparties."
    ),
    "criteria": {
        "explore_right": "cross to the right side of the road (around 169,150) to discover unknown vendors",
        "explore_left": "walk to the left side of the road (around 144,150) to discover unknown vendors",
        "explore_up": "walk further up the street (north) to find more shops",
        "explore_down": "walk further down the street (south) to find more shops",
        "exploit_known": "stay put and execute the already-matched profitable pairs now",
    },
}


def explore_question(arbs):
    """Build a Laya question seeded with the current arbitrage picture."""
    top = arbs[:5] if arbs else []
    q = dict(EXPLORATION_Q)
    # arbs may be partial (defensive): read keys with defaults
    q["instructions"] += " Known top pairs: " + (
        "; ".join("%s (spread %s, gross %s)" % (
            a.get("item", "?"), a.get("spread", "?"), a.get("gross", "?"))
            for a in top) if top else "none")
    return q


def choose_exploration(policy, arbs):
    """Ask Laya where to explore. Returns one of the criteria keys."""
    try:
        r = policy.predict(json.dumps({"arbs_found": len(arbs)}, ensure_ascii=False),
                           {"explore": explore_question(arbs)})
        pick = r["answers"]["explore"]["choice"]
        # accept only our known keys
        if pick in ("explore_right", "explore_left", "explore_up", "explore_down", "exploit_known"):
            return pick
    except Exception:
        pass
    # heuristic fallback: if few arbs, explore; else exploit
    return "exploit_known" if len(arbs) >= 3 else "explore_right"


# ---------- execution helpers (drive npc_ops / inventory via api) ----------

def zeny(api):
    """Current zeny from the client."""
    import js_ops
    z = js_ops.run(api, "return window.roAgent.modules.Session.zeny;")
    return z if isinstance(z, int) else 0


def execute_pair(api, arb, log=print, dry=False):
    """Buy `max_qty` of arb['item'] from buy_from, then sell to sell_to.
    Returns {ok, stage, bought, sold, profit}."""
    import npc_ops, js_ops, inventory_ops
    item, qty = arb["item"], arb["max_qty"]
    before = zeny(api)

    # 1. open the sell shop (buy_from) and buy
    sign = arb["buy_from"]["sign"]
    ok = npc_ops.open_shop_by_text(api, sign, log=log)
    if not ok.get("open"):
        return {"ok": False, "stage": "open_buy", "error": ok.get("reason")}
    if dry:
        return {"ok": True, "stage": "dry", "item": item, "qty": qty, "ask": arb["ask"]}
    # actual buy: same path validated earlier (drop -> qty -> .btn.buy)
    bought = _buy_item(api, item, qty, log=log)
    if not bought.get("ok"):
        return {"ok": False, "stage": "buy", "error": bought.get("error")}
    close_store(api); time.sleep(0.5)

    # 2. open the buy shop (sell_to) and sell
    sign2 = arb["sell_to"]["sign"]
    ok2 = npc_ops.open_shop_by_text(api, sign2, log=log)
    if not ok2.get("open"):
        return {"ok": False, "stage": "open_sell", "error": ok2.get("reason")}
    sold = _sell_item(api, item, qty, log=log)
    close_store(api); time.sleep(0.5)

    after = zeny(api)
    profit = after - before
    return {"ok": bool(sold.get("ok")), "stage": "done", "item": item,
            "bought": qty, "sold": qty, "profit": profit,
            "zeny_before": before, "zeny_after": after}



# KNOWN BLOCKER (sell direction): the InputBox qty prompt for SELLING to a buying
# store mounts with its component root DISCONNECTED (getComponent('InputBox').getRoot()
# .isConnected === false). InputBox.validate() reads that root's input, which stays "",
# so onSubmitRequest(0) fires and transferItem never runs -> item stays in Available.
# Setting the VISIBLE input's value works (sticks at 20) but validate() reads the stale
# root's input, not the visible one. BUY direction works (root stays connected).
# Fix path: driver command calling NpcStore.transferItem directly, bypassing InputBox.

def _buy_item(api, item_name, qty, log=print):
    """Buy `qty` of `item_name` from the open shop in ONE atomic js call:
    find row -> drop to cart -> set qty -> click InputBox OK -> click buy.

    Doing this across multiple api round-trips lets the dialog go stale and
    close (the UI times out between calls). Everything below runs inside the
    page in a single eval, so no round-trip gaps."""
    import js_ops, js_ops as _jo
    before = zeny(api)
    body = """
      const name = arg.name, qty = arg.qty;
      const find = (sel) => { let r=null; const v=root=>root.querySelectorAll(sel).forEach(e=>{
        if(e.shadowRoot){v(e.shadowRoot);return;} if(!r && __visible(e)) r=e;}); v(document); return r; };
      const rect = el => el ? __rect(el) : null;
      // 1. find the item row
      let row=null;
      const v1=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){v1(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return;
        if(e.classList&&e.classList.contains('item')){const n=e.querySelector('.name');
          if(n&&n.textContent.trim()===name)row=e;}});
      v1(document);
      if(!row) return {stage:'find', error:'item not in shop: '+name};
      const idx = parseInt(row.getAttribute('data-index'),10);
      // 2. drop into OutputWindow
      let output=null;
      const v2=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){v2(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return; const cls=(e.className||'').toString();
        if(/OutputWindow/.test(cls)&&__visible(e)&&!output)output=e;});
      v2(document);
      if(!output) return {stage:'drop', error:'no OutputWindow'};
      const dt=new DataTransfer();
      dt.setData('Text', JSON.stringify({type:'item',from:'NpcStore',container:'InputWindow',index:idx}));
      output.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
      // 3. qty InputBox appears -> set value, then click its ui-button (OK)
      //    InputBox.submit -> validate() reads input.value -> onSubmitRequest
      //    We wait briefly (synchronously impossible), so find it now:
      let input=null, okBtn=null;
      const v3=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){v3(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return; if(!__visible(e))return;
        if(e.tagName==='INPUT'&&/^\d+$/.test(e.value||''))input=e;
        if(e.tagName==='UI-BUTTON'&&!okBtn)okBtn=e;   // InputBox's OK button
        if(e.tagName==='BUTTON'&&!okBtn&&(e.textContent||'').trim()==='OK')okBtn=e;});
      v3(document);
      if(input){
        const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        s.call(input, String(qty));
        input.dispatchEvent(new Event('input',{bubbles:true}));
        input.dispatchEvent(new Event('change',{bubbles:true}));
      }
      if(okBtn){ okBtn.click(); }   // real element click -> validate()
      return {stage:'qty', idx, hadInput:!!input, hadOk:!!okBtn};
    """
    q = js_ops.run(api, body, arg={"name": item_name, "qty": qty})
    log("  BUY qty-step: %s" % json.dumps(q))
    time.sleep(0.9)   # cart populates after InputBox submits
    # 4. click the buy button
    btn = js_ops.run(api, """
      let b=null; const v=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){v(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return; const cls=(e.className||'').toString();
        if(/btn buy/.test(cls)&&__visible(e)&&!b)b=e;});
      v(document); return b?__rect(b):null;""")
    if not btn:
        log("  BUY: no buy button (cart empty?)")
        return {"ok": False, "error": "no buy button"}
    api("click", [str(btn["x"]), str(btn["y"])])
    time.sleep(1.8)
    after = zeny(api)
    spent = before - after
    log("  BUY: spent %d (zeny %d -> %d)" % (spent, before, after))
    return {"ok": spent > 0, "spent": spent, "before": before, "after": after}


def _sell_item(api, item_name, qty, log=print):
    """Sell `qty` of item to the open buying store. Buying stores: click item
    -> qty -> submit (sell path mirrors buy but targets a BUYING_STORE)."""
    import js_ops
    row = js_ops.run(api, """
      let el=null; const visit=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){visit(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return;
        if(e.classList&&e.classList.contains('item')){const n=e.querySelector('.name');
          if(n&&n.textContent.trim()===arg) el=e;}});
      visit(document); return el?{index:parseInt(el.getAttribute('data-index'),10)}:{error:'not found'};""",
        arg=item_name)
    if isinstance(row, dict) and row.get("error"):
        return {"ok": False, "error": row["error"]}
    # same drop->qty->submit flow (buying store accepts player items)
    js_ops.run(api, """
      let item=null,output=null;
      const visit=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){visit(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return; const cls=(e.className||'').toString();
        if(e.classList&&e.classList.contains('item')&&parseInt(e.getAttribute('data-index'),10)===arg&&!item)item=e;
        if(/OutputWindow/.test(cls)&&__visible(e)&&!output)output=e;});
      visit(document);
      if(!item||!output) return {ok:false};
      const dt=new DataTransfer();
      dt.setData('Text',JSON.stringify({type:'item',from:'NpcStore',container:'InputWindow',index:arg}));
      output.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
      return {ok:true};""", arg=row.get("index"))
    time.sleep(1.2)
    js_ops.run(api, """
      let input=null; const visit=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){visit(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return; if(e.tagName==='INPUT'&&__visible(e)&&/^\\d+$/.test(e.value))input=e;});
      visit(document); if(!input)return{error:'no qty'};
      const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
      s.call(input,String(arg)); input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new Event('change',{bubbles:true})); return{ok:true};""", arg=qty)
    time.sleep(0.8)
    btn = js_ops.run(api, """
      let b=null; const visit=root=>root.querySelectorAll('*').forEach(e=>{ if(e.shadowRoot){visit(e.shadowRoot);return;}
        if(e.tagName==='STYLE')return; const cls=(e.className||'').toString();
        if(/btn (sell|buy)/.test(cls)&&__visible(e)&&!b)b=e;});
      visit(document); return b?__rect(b):null;""")
    if not btn:
        return {"ok": False, "error": "no sell button"}
    z0 = zeny(api)
    api("click", [str(btn["x"]), str(btn["y"])])
    time.sleep(2.2)
    z1 = zeny(api)
    return {"ok": z1 > z0, "earned": z1 - z0}


# ---------- exploration loop (wired into agent.py) ----------

# Prontera street vendor rows (user-provided): the left-side shops sit around
# (144,150) and the right-side shops around (169,150), same row, x 25 apart.
# explore_right crosses to the right row; up/down walk along the street for more.
_EXPLORE_STEPS = {
    "explore_right": [(169, 150), (172, 152), (166, 148)],   # right-side vendor row
    "explore_left":  [(144, 150), (141, 152), (147, 148)],   # left-side vendor row
    "explore_up":    [(156, 130), (156, 120), (156, 110)],   # north up the street
    "explore_down":  [(156, 170), (156, 180), (156, 190)],   # south down the street
    "exploit_known": [],                                     # stay put
}


def explore_target(direction, step_idx=0):
    """Walk cell for a direction at progressive step (cycles). None = stay."""
    path = _EXPLORE_STEPS.get(direction, [])
    if not path:
        return None
    return path[step_idx % len(path)]


def walk_explore(api, direction, step_idx=0, log=print):
    """Walk the character one explore step. Returns (walked, xy)."""
    xy = explore_target(direction, step_idx)
    if not xy:
        return False, None
    log("  EXPLORE %s -> (%d,%d)" % (direction, xy[0], xy[1]))
    api("walk", [str(xy[0]), str(xy[1])])
    time.sleep(2.5)
    return True, xy


def merge_shops(old_shops, new_shops):
    """Merge two dumps by sign text, keeping the union (so shops seen across
    multiple street positions all count). Returns a shops list for match()."""
    seen = {}
    for s in (old_shops or []) + (new_shops or []):
        key = s.get("sign") or s.get("seller")
        if not key:
            continue
        prev = seen.get(key)
        # prefer the entry that actually opened with items
        if (not prev) or (not prev.get("open") and s.get("open")):
            seen[key] = s
    return list(seen.values())


def trade_state(arbs, direction, step_idx, zeny_now=None):
    """Compact state handed to Laya each trade step."""
    return {
        "zeny": zeny_now,
        "arbs_found": len(arbs),
        "top_arbs": [{"item": a["item"], "spread": a["spread"], "gross": a["gross"],
                      "buy_from": a["buy_from"]["sign"], "sell_to": a["sell_to"]["sign"]}
                     for a in arbs[:5]],
        "exploring": direction,
        "explore_step": step_idx,
        "hint": "right side of the road is unmapped; more buy/sell vendors likely there",
    }


TAG_BODY = """
  __deep('[data-agent-target]').forEach(e => e.removeAttribute('data-agent-target'));
  const t = __deep('.EntityRoom .title, .EntityRoom .overlay').find(
      e => __visible(e) && (e.textContent||'').trim() === arg);
  if (!t) return { error: 'title not found: ' + arg };
  (t.closest('button')||t).setAttribute('data-agent-target','1');
  return { ok: true };
"""


def open_sign(api, sign_text):
    """Open a shop sign the PROVEN way: shadow-tag its button, then a real-input
    double-click via clicksel --double (raw pixel click doesn't open it)."""
    import js_ops, npc_ops
    js_ops.run(api, TAG_BODY, arg=sign_text)
    r = api("clicksel", ["[data-agent-target]", "--double"])
    if not (isinstance(r, dict) and r.get("ok")):
        return None
    time.sleep(1.3)
    return npc_ops.store_state(api)


# pacing: a rapid open/esc/open burst crashed the session; keep it slow.
DUMP_GAP = 2.5         # seconds between signs
DUMP_SESSION_CHECK = 8 # check the session every N signs


def close_store(api):
    """Close the open shop window by clicking its own cancel button (.btn cancel).
    NEVER press esc — esc opens the game settings window (blocks the UI and
    flips inGame to false). Returns True if a cancel button was clicked."""
    import js_ops
    btn = js_ops.run(api, """
      let b=null;
      const visit=root=>root.querySelectorAll('*').forEach(el=>{
        if(el.shadowRoot){visit(el.shadowRoot);return;}
        if(el.tagName==='STYLE')return;
        const cls=(el.className||'').toString();
        if(/btn cancel/.test(cls)&&__visible(el)&&!b)b=el;});
      visit(document); return b?__rect(b):null;""")
    if not (isinstance(btn, dict) and btn.get("x")):
        return False
    api("click", [str(btn["x"]), str(btn["y"])])
    time.sleep(0.8)
    return True


def dump_street(api, log=print, limit=60, gap=None):
    """Dump visible shop signs SLOWLY -> shops list (only those that open).

    Pacing (user requirement — fast open/close loops crash the game):
      - `gap` seconds between each sign (default DUMP_GAP)
      - esc only when a store is actually open (spamming esc with no window
        open blocks the UI)
      - a session check every DUMP_SESSION_CHECK signs so we stop before
        wedging the client if it drops mid-run
    """
    import npc_ops, js_ops
    gap = DUMP_GAP if gap is None else gap
    signs = npc_ops.find_shops(api)[:limit]
    shops = []
    for i, s in enumerate(signs):
        time.sleep(gap)                 # pace BEFORE each open
        st = open_sign(api, s["text"])
        opened = isinstance(st, dict) and st.get("open")
        if opened:
            entry = dict(s)
            entry["sign"] = entry.get("sign") or s.get("text")
            entry.update({"open": True, "seller": st.get("seller"),
                          "items": st.get("items", [])})
            shops.append(entry)
            close_store(api)            # click the window's cancel btn (NEVER esc)
            time.sleep(gap)             # pace AFTER close too
        # session check on a cadence (and always after an open attempt)
        if (i + 1) % DUMP_SESSION_CHECK == 0 or opened:
            alive = js_ops.run(api, "return true;")
            if not (alive is True or (isinstance(alive, dict) and not alive.get("error"))):
                log("  DUMP: session lost at %d/%d -> stop" % (i + 1, len(signs)))
                break
        if (i + 1) % DUMP_SESSION_CHECK == 0:
            log("  DUMP progress: %d/%d opened=%d" % (i + 1, len(signs), len(shops)))
    log("  DUMP: %d/%d shops opened" % (len(shops), len(signs)))
    return shops


def trade_step(api, policy, sweep, log=print, execute_trades=False):
    """One iteration of the exploration loop:
      1. dump current street -> merge into the sweep accumulator
      2. re-match -> arbs
      3. ask Laya where to explore next
      4. walk one step (exploit_known = stay)
    `sweep` is a dict {shops:[], direction, step_idx} mutated across calls.
    Returns (arbs, direction) after updating sweep."""
    new = dump_street(api, log=log)
    sweep["shops"] = merge_shops(sweep.get("shops"), new)
    arbs = match(sweep["shops"])
    z = zeny(api)
    st_state = trade_state(arbs, sweep.get("direction", "explore_right"),
                           sweep.get("step_idx", 0), zeny_now=z)
    direction = choose_exploration(policy, arbs)
    sweep["direction"] = direction
    log("  TRADE: %d shops known, %d arbs, zeny %s -> Laya says %s" % (
        len(sweep["shops"]), len(arbs), z, direction))
    if direction != "exploit_known":
        sweep["step_idx"] = sweep.get("step_idx", 0) + 1
        walk_explore(api, direction, sweep["step_idx"], log=log)
    elif execute_trades and arbs:
        a = arbs[0]
        log("  EXEC top arb: %s (gross %s)" % (a["item"], a["gross"]))
        r = execute_pair(api, a, log=log)
        log("    -> %s" % json.dumps({k: r[k] for k in r if k != "stage"}))
    return arbs, direction


if __name__ == "__main__":
    shops = load()
    arbs = match(shops)
    print("loaded %d shops -> %d arbitrage pairs" % (len(shops), len(arbs)))
    for a in arbs[:10]:
        print("  %-24s ask=%-8s bid=%-8s spread=%-6s qty<=%-4s gross=%s" % (
            a["item"][:24], a["ask"], a["bid"], a["spread"], a["max_qty"], a["gross"]))
    print("total gross:", sum(a["gross"] for a in arbs))
