#!/usr/bin/env python
# High-level operation sequences tying the four new capabilities together.
# Each op is callable independently; the agent (or a test) runs them in order.
# Requires the built app (learn/unequip/weight/deal/sell commands installed).
import json, time, sys

def _log(msg):
    print(msg, flush=True)

# ---- Op 1: spend skill points toward a preset (novice first) ----
def op_train_skills(api, preset="novice", log=_log):
    import skill_ops
    # learn path: spend until the preset is reached or points run out
    learned, msg = skill_ops.spend(api, preset, log=log)
    log("  " + msg)
    return learned, msg

# ---- Op 2: Job Master -> change to `job` (after skills spent) ----
def op_job_change(api, job="Swordman", log=_log):
    import npc_ops
    ok, msg = npc_ops.job_change(api, job, log=log)
    log("  " + msg)
    return ok, msg

# ---- Op 3: inventory — report weight, equip/unequip, use ----
def op_weight(api, log=_log):
    import inventory_ops
    w = inventory_ops.weight(api)
    log("  weight: %s/%s (%s%%)" % (w.get("weight"), w.get("max_weight"), w.get("pct")))
    return w

def op_equip(api, item_id, log=_log):
    import inventory_ops
    r = inventory_ops.equip(api, item_id)
    log("  equip %s -> %s" % (item_id, json.dumps(r)[:100]))
    return r

def op_unequip(api, item_id, log=_log):
    import inventory_ops
    r = inventory_ops.unequip(api, item_id)
    log("  unequip %s -> %s" % (item_id, json.dumps(r)[:100]))
    return r

def op_use(api, item_id, log=_log):
    import inventory_ops
    r = inventory_ops.use(api, item_id)
    log("  use %s -> %s" % (item_id, json.dumps(r)[:100]))
    return r

# ---- Op 4: sell to Tool Dealer (weight pressure from @autoloot) ----
def op_sell(api, item_name=None, log=_log):
    """Open Tool Dealer, pick sell, sell `item_name` (or list sellable)."""
    import npc_ops, inventory_ops
    # walk to Tool Dealer + interact (opens buy/sell/cancel WinDeal)
    dlg = npc_ops.interact_npc(api, "Tool Dealer", log=log)
    if not dlg.get("open") and not _deal_open(api):
        # WinDeal isn't a dialog; detect it separately
        pass
    # pick 'sell' in the deal popup
    r = api("deal", ["sell"])
    log("  deal sell -> %s" % json.dumps(r)[:120])
    time.sleep(1.0)
    if item_name is None:
        # list what's sellable
        lst = api("sell")
        log("  sellable: %s" % json.dumps(lst)[:300])
        return lst
    # sell one item
    r = api("sell", [str(item_name)])
    log("  sell %s -> %s" % (item_name, json.dumps(r)[:150]))
    return r

def _deal_open(api):
    # the WinDeal popup has no dialog/prompt surface; assume open after interact
    return True

# ---- The full sequence ----
def run_full(api, job="Swordman", log=_log):
    """Spend novice skills, then job change. Inventory/sell are separate calls."""
    log("=== OP1: train skills (novice preset) ===")
    learned, m1 = op_train_skills(api, "novice", log=log)
    log("=== OP2: job change -> %s ===" % job)
    ok, m2 = op_job_change(api, job, log=log)
    return {"skills": m1, "job": m2, "job_ok": ok}

if __name__ == "__main__":
    sys.path.insert(0, r"D:\hermes\laya-agent")
    from core import api
    # demo: read weight (safe, works on new build)
    op_weight(api)
