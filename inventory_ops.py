#!/usr/bin/env python
# Inventory + shop operations: weight, equip/unequip, use consumables, and
# (once a shop is open) selling loot. Thin wrappers over the game API commands
# added in electron/agent-driver.js (learn, unequip, weight, use, equip).
import time

def weight(api):
    """{weight, max_weight, pct} — carry weight vs limit."""
    r = api("weight")
    if isinstance(r, dict) and "weight" in r:
        return r
    return {"weight": 0, "max_weight": 0, "pct": 0, "error": r.get("error") if isinstance(r, dict) else "?"}

def near_weight_limit(api, threshold=90):
    """True when carry weight >= threshold% of max (loot is about to block us)."""
    w = weight(api)
    return w.get("pct", 0) >= threshold

def equip(api, item_id):
    return api("equip", [str(item_id)])

def unequip(api, item_id):
    return api("unequip", [str(item_id)])

def use(api, item_id):
    return api("use", [str(item_id)])

def sell(api, shop_npc, items, radius="15"):
    """Sell items to an NPC shop: walk to it, interact, then sell.

    `items` is a list of {nameid, amount} (or a single item id). The actual
    sell packet (CZ.PC_SELL_ITEMLIST) needs the sell list the server pushes
    when the shop opens — handled by the agent-driver `sell` command once the
    shop is open. This high-level op does the walk + interact first.
    """
    # 1. talk to the shop NPC (name or gid)
    r = api("interact", [str(shop_npc)])
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "stage": "interact", "error": r["error"]}
    time.sleep(1.5)
    # 2. read the shop (the server pushes a sell list; NpcStore is now open)
    # The sell command reads that list and submits. Returns the store state.
    return api("sell", [json_items(items)])

def json_items(items):
    import json
    if isinstance(items, int):
        return json.dumps([{"nameid": items, "amount": 1}])
    return json.dumps(items)
