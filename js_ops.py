#!/usr/bin/env python
# Shared helper for running page JS through the agent's `js` command.
#
# The `js` command (electron/agent-driver.js) evaluates a body inside
#   (async (arg) => { PAGE_HELPERS <body> })(<arg JSON>)
# with three helpers in scope:
#   __deep(selector)  -> all matches, walking shadow roots (roBrowser uses shadow DOM)
#   __visible(el)     -> false if display:none / hidden / zero-size
#   __rect(el)        -> {x,y} center of the element in CLICK coordinates
# and `arg` bound to the second api arg. So every body must `return` its value.
#
# window.roAgent exposes: player, entities, skills, chat, useSkill, net, effects,
# mouse, modules: { Session, Camera, Renderer, MapRenderer, EntityManager, Entity,
#                   Altitude, Mouse, DB, SkillInfo, Network, PACKET, PACKETVER,
#                   UIManager }.
import json

# One-liner bodies (statements; must return). Keep them small and pure.
BODIES = {
    # ---- game state ----
    "weight": """
        const S = window.roAgent.modules.Session.Entity;
        const w = S.weight || 0, m = S.max_weight || 0;
        return { weight: w, max_weight: m, pct: m ? Math.round(100*w/m) : 0 };
    """,
    "player": """
        const p = window.roAgent.player;
        return p ? { name: p.name, job: p.job, gid: p.gid,
                     position: p.position, hp: p.hp, map: p.map || null } : null;
    """,
    "skills": """
        return window.roAgent.skills();
    """,
    # ---- shop / merchant titles (the S> and B> labels) ----
    "shops": """
        // Each merchant's shop sign is a <button> with .title (+ .overlay) inside
        // an EntityRoom clone. Read the title text + its rect for clicking.
        return __deep('.EntityRoom .title, .EntityRoom .overlay')
            .filter(__visible)
            .map(el => {
                const r = el.getBoundingClientRect();
                return {
                    text: (el.textContent || '').trim(),
                    x: Math.round(r.left + r.width/2),
                    y: Math.round(r.top + r.height/2),
                };
            })
            .filter(s => s.text);
    """,
    # ---- which UI windows are open ----
    "open_windows": """
        const names = ['NpcStore','VendingShop','Vending','Inventory','Equipment',
                       'WinDeal','NpcBox','NpcMenu','SkillList','WinStats'];
        const out = {};
        for (const n of names) {
            try {
                const c = window.roAgent.modules.UIManager.getComponent(n);
                if (!c) { out[n] = false; continue; }
                const root = c.getRoot ? c.getRoot() : (c._host || null);
                out[n] = !!(root && root.isConnected && __visible(root));
            } catch (e) { out[n] = false; }
        }
        return out;
    """,
    # ---- NpcStore (shop window) contents once open ----
    "store_items": """
        try {
            const c = window.roAgent.modules.UIManager.getComponent('NpcStore');
            if (!c) return { open: false, items: [] };
            const root = c.getRoot ? c.getRoot() : c._host;
            const open = !!(root && root.isConnected && __visible(root));
            // store type: vend (sell vendor) vs buy (buying store) vs npc
            let type = null;
            try { type = (c.getType && c.getType()) || c._type || null; } catch (e) {}
            // the seller/shop name if shown
            let seller = null;
            try { const el = root && root.querySelector('.seller'); seller = el ? el.textContent : null; } catch (e) {}
            const items = __deep('#NpcStore .content .item, .content .item[data-index]')
                .filter(__visible)
                .map(el => {
                    const g = sel => { const e = el.querySelector(sel); return e ? (e.textContent||'').trim() : ''; };
                    const r = el.getBoundingClientRect();
                    return {
                        index: el.getAttribute('data-index'),
                        name: g('.name'),
                        amount: g('.amount'),
                        price: g('.price'),
                        currency: g('.unity') || 'Z',
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top + r.height/2),
                    };
                })
                .filter(it => it.name);
            return { open, type, seller, items };
        } catch (e) { return { open: false, error: String(e), items: [] }; }
    """,
    # ---- inventory items (by ITID) ----
    "inventory": """
        try {
            const inv = window.roAgent.modules.UIManager.getComponent('Inventory');
            if (!inv || !inv.list) return { items: [] };
            return { items: inv.list.map(it => ({
                itid: it.ITID, index: it.index, count: it.count,
                location: it.location, name: (it.name || it.Name || ''),
            })) };
        } catch (e) { return { items: [], error: String(e) }; }
    """,
    # ---- equipped items ----
    "equipped": """
        try {
            const inv = window.roAgent.modules.UIManager.getComponent('Inventory');
            const eq = inv && inv.equippedItems ? inv.equippedItems : [];
            // equippedItems holds indices; resolve against list if present
            const list = (inv && inv.list) ? inv.list : [];
            return { indices: eq, count: eq.length };
        } catch (e) { return { indices: [], error: String(e) }; }
    """,
}


def run(api, name, arg=None, t=30):
    """Run a named body (or raw JS) via the `js` command. Returns the value,
    or {'error': ...} if the command is missing/failed."""
    body = BODIES.get(name, name)  # allow raw JS too
    r = api("js", [body] + ([json.dumps(arg)] if arg is not None else []), t=t)
    if isinstance(r, dict) and "error" in r and "result" not in r:
        return {"error": r.get("error"), "commands": r.get("commands")}
    if isinstance(r, dict):
        return r.get("result", r)
    return r


def raw(api, body, arg=None, t=30):
    """Run a raw JS body (statements, must return) via `js`."""
    return run(api, body, arg, t=t)
