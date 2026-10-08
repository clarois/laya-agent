#!/usr/bin/env python
# Dump player-shop contents (sell + buy vendors) to JSON for the database.
#
# Flow per vendor: find the shop sign in the DOM -> shadow-aware tag its button
# -> `clicksel <sel> --double` (real input double-click opens the shop) ->
# `store_items` reads .content > .item rows (name/amount/price/currency) ->
# close, move to the next.
#
# Usage: python dump_shops.py [--out shops.json] [--max-sell N] [--max-buy N]
import argparse, json, sys, time
sys.path.insert(0, r"D:\hermes\laya-agent")
import js_ops, npc_ops

# `api` comes from core, which reads connection.json at import time (needs a live
# game). Import it lazily inside the functions that use it so this module imports
# cleanly without the game running.


def _api():
    from core import api
    return api

TAG_BODY = """
  __deep('[data-agent-target]').forEach(e => e.removeAttribute('data-agent-target'));
  const titles = __deep('.EntityRoom .title, .EntityRoom .overlay');
  const t = titles.find(e => __visible(e) && (e.textContent||'').trim() === arg);
  if (!t) return { error: 'title not found: ' + arg };
  const btn = t.closest('button') || t;
  btn.setAttribute('data-agent-target','1');
  const r = btn.getBoundingClientRect();
  return { x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2) };
"""


def open_and_read(sign_text, api=None, log=print):
    """Tag + double-click a shop sign, then read its contents."""
    api = api or _api()
    info = js_ops.run(api, TAG_BODY, arg=sign_text)
    if isinstance(info, dict) and info.get("error"):
        return {"ok": False, "error": info["error"]}
    r = api("clicksel", ["[data-agent-target]", "--double"])
    if not (isinstance(r, dict) and r.get("ok")):
        return {"ok": False, "error": "clicksel failed: %s" % r}
    time.sleep(1.5)
    st = npc_ops.store_state(api)
    api("key", ["esc"])  # close for the next one
    time.sleep(0.8)
    return {"ok": True, "sign": sign_text, "store": st}


def dump(max_sell=5, max_buy=5, api=None):
    api = api or _api()
    out = {"generated_by": "dump_shops.py", "shops": []}
    for kind, limit in (("sell", max_sell), ("buy", max_buy)):
        signs = npc_ops.shops_of_kind(api, kind)[:limit]
        for s in signs:
            res = open_and_read(s["text"], api=api)
            entry = {"kind": kind, "sign": s["text"]}
            if res.get("ok"):
                st = res["store"]
                entry.update({
                    "seller": st.get("seller"),
                    "open": st.get("open"),
                    "items": st.get("items", []),
                })
                print("[%s] %-30s -> %s (%d items)" % (
                    kind, s["text"][:30], st.get("seller"), len(st.get("items", []))))
            else:
                entry["error"] = res.get("error")
                print("[%s] %-30s -> FAILED %s" % (kind, s["text"][:30], res.get("error")))
            out["shops"].append(entry)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="shops.json")
    ap.add_argument("--max-sell", type=int, default=5)
    ap.add_argument("--max-buy", type=int, default=5)
    a = ap.parse_args()
    data = dump(a.max_sell, a.max_buy)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    total = sum(len(s.get("items", [])) for s in data["shops"])
    ok = sum(1 for s in data["shops"] if s.get("open"))
    print("\nwrote %s: %d/%d shops open, %d total items" % (
        a.out, ok, len(data["shops"]), total))
