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
        // Read an open shop window (player Merchant Shop = WinVendingStore, or an
        // NPC store = NpcStore): seller title + item rows (name/amount/price).
        // Rows live at .content > .item > .name/.price/.amount across shadow roots.
        try {
            const rows = [];
            const g = (el, sel) => { const e = el.querySelector(sel); return e ? (e.textContent||'').trim() : ''; };
            let seller = null, type = null;
            const visit = root => {
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) visit(el.shadowRoot);
                    if (el.classList && el.classList.contains('item')) {
                        const name = g(el, '.name');
                        if (name) rows.push({
                            index: el.getAttribute('data-index'),
                            name,
                            amount: g(el, '.amount') || g(el, '.count'),
                            price: g(el, '.price') || g(el, '.expanded_price') || g(el, '.currency'),
                            currency: g(el, '.unity') || 'Z',
                        });
                    }
                    // seller title (WinVendingStore .seller) + store type hint
                    if (el.classList && el.classList.contains('seller') && !seller)
                        seller = (el.textContent||'').trim().replace(/^Merchant Shop *- */i,'');
                });
            };
            visit(document);
            // open = any shop window visible OR we found rows
            const openWindows = [];
            for (const n of ['NpcStore','VendingShop','Vending']) {
                try { const c = window.roAgent.modules.UIManager.getComponent(n);
                      const rt = c && (c.getRoot ? c.getRoot() : c._host);
                      if (rt && rt.isConnected && __visible(rt)) openWindows.push(n); } catch (e) {}
            }
            const open = rows.length > 0 || openWindows.length > 0;
            // type: buy-vendor if a buying-store window; sell-vendor if Merchant Shop
            if (openWindows.includes('VendingShop') || seller) type = 'vend';
            else if (openWindows.includes('NpcStore')) type = 'npc';
            return { open, type, seller, openWindows, count: rows.length, items: rows };
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
