#!/usr/bin/env python
# NPC interactions: Job Master (job change), shop NPCs, and player merchants.
#
# Reads the DOM via the `js` command instead of pixel-guessing:
#   - Merchant shop signs: __deep('.EntityRoom .title') -> text + rect
#   - NPCs: window.roAgent.entities() -> click coords (no hover spiral)
#   - Clicking: __rect(el) gives exact click coordinates
# Dialog flow (Job Master from standart-npc mod): skill points spent FIRST
# (Check_SkillPoints), then menu -> confirm dialog. Dialogs go STALE (~10s idle
# shows only Close) so act fast between reads.
import time
import js_ops

# NPC coordinates from the standart-npc mod files.
JOB_MASTER = (157, 195)   # prontera jobmaster.txt
TOOL_DEALER = (143, 178)  # prontera tool_dealer.txt

def _dialog(api):
    r = api("dialog")
    return r if isinstance(r, dict) else {}

def walk_to(api, x, y):
    r = api("walk", [str(x), str(y)])
    time.sleep(1.0)
    return r

def interact(api, spec):
    """spec = gid or name. Returns the dialog opened."""
    r = api("interact", [str(spec)])
    time.sleep(1.2)
    return _dialog(api)

def read_dialog(api):
    """Current dialog: text + menu options (if any)."""
    return _dialog(api)

def dialog_next(api):
    r = api("next")
    time.sleep(0.8)
    return _dialog(api)

def choose(api, n):
    r = api("choose", [str(n)])
    time.sleep(1.0)
    return _dialog(api)

def close(api):
    return api("close")

def parse_menu(dlg):
    """Extract menu options ['Swordman','Mage',...] from a dialog result."""
    if not dlg:
        return []
    menu = dlg.get("menu") or []
    if isinstance(menu, list):
        out = []
        for m in menu:
            if isinstance(m, dict):
                out.append(m.get("text", ""))
            else:
                out.append(str(m))
        return out
    return []


def find_shops(api):
    """List every visible player-shop sign from the DOM: [{text,kind,x,y}, ...].
    text is the title (e.g. 'S> Steel 5.9k', 'Coratae is buying'); kind is
    'sell' (S>) / 'buy' (B>/buys/buying) / 'shop' (other). Coordinates are
    click-space centers, ready for api('click')."""
    shops = js_ops.run(api, "shops")
    if isinstance(shops, dict) and shops.get("error"):
        return []
    if not isinstance(shops, list):
        return []
    for s in shops:
        t = (s.get("text") or "").lower()
        # buy: explicit buy intent (B>, buying, buys, WTB = want-to-buy)
        if (t.startswith("b>") or "buy" in t or "wtb" in t
                or "want to buy" in t):
            s["kind"] = "buy"
        # sell: explicit sell intent (S>, wts, selling) or a plain vendor sign
        # (most signs advertise items for sale; pure greetings sort to 'shop')
        elif (t.startswith("s>") or "wts" in t or "sell" in t
              or "for sale" in t):
            s["kind"] = "sell"
        elif _looks_like_greeting(t):
            s["kind"] = "shop"
        else:
            s["kind"] = "sell"  # a named vendor sign = selling something
    return shops


def _looks_like_greeting(t):
    """True for chatty signs that aren't really item vendors."""
    greetings = ("happy hunting", "come on", "lol", "hi", "hello",
                 "good luck", "glhf", "nice", "cool")
    return any(t.startswith(g) or t == g for g in greetings)


def shops_of_kind(api, kind):
    """Shops classified by kind: 'sell' (vendors selling), 'buy' (buying stores)."""
    return [s for s in find_shops(api) if s.get("kind") == kind]


def shop_by_text(api, substring):
    """First shop whose title contains `substring` (case-insensitive)."""
    sub = substring.lower()
    for s in find_shops(api):
        if sub in (s.get("text") or "").lower():
            return s
    return None


def open_shop(api, sign, log=print):
    """Click a shop sign (from find_shops) to open its buy/sell window.
    Returns the NpcStore state after the click."""
    if isinstance(sign, dict):
        x, y = sign.get("x"), sign.get("y")
    else:
        x, y = sign
    log("  SHOP click sign at (%s,%s)" % (x, y))
    api("click", [str(x), str(y)])
    time.sleep(1.5)
    return store_state(api)


def store_state(api):
    """Read the NpcStore (shop) window: open? what items? via DOM."""
    st = js_ops.run(api, "store_items")
    if isinstance(st, dict):
        return st
    return {"open": False, "items": []}


def find_npc(api, name):
    """Locate an NPC by name using window.roAgent.entities() (DOM-backed, no
    pixel hover-spiral). Returns (x, y) click coords or None."""
    body = """
        const ents = window.roAgent.entities();
        const e = ents.find(e => e.type === 'NPC' && (e.name||'').startsWith(arg));
        if (!e || !e.click) return null;
        return { x: e.click.x, y: e.click.y, gid: e.gid };
    """
    r = js_ops.run(api, body, arg=name)
    if isinstance(r, dict) and r.get("x") is not None:
        return (r["x"], r["y"])
    return None

def interact_npc(api, name, log=print):
    """Find an NPC's click point via DOM and click it to open its dialog."""
    px = find_npc(api, name)
    if not px:
        return {"ok": False, "reason": "npc '%s' not found in entities" % name}
    log("  NPC %s at (%s,%s)" % (name, px[0], px[1]))
    api("click", [str(px[0]), str(px[1])])
    time.sleep(1.5)
    return _dialog(api)

def job_change(api, target_job_name, max_steps=12, log=print):
    """Walk to Job Master and job-change to `target_job_name` (e.g. 'Swordman').

    Assumes skill points are already spent (the NPC refuses otherwise).
    Dialog flow: greeting -> (maybe level req message) -> 'Select a job' menu
    -> pick option -> 'Do you want to change into X?' confirm -> yes -> done.
    Returns (ok, message).
    """
    # 1. go to the Job Master
    walk_to(api, *JOB_MASTER)
    # 2. talk to it via pixel-accurate interact (NPCs overlap; name click is unreliable)
    dlg = interact_npc(api, "Job Master", log=log)
    if not dlg.get("open"):
        return False, "job_change: Job Master dialog did not open: %s" % dlg.get("reason", "")

    for step in range(max_steps):
        menu = parse_menu(dlg)
        text = (dlg.get("text") or "")
        # refusal: not enough level / skill points pending
        if "skill points" in text.lower():
            return False, "job_change: spend skill points first (NPC refuses)"
        if "more base levels" in text.lower() or "level requirement" in text.lower():
            return False, "job_change: level requirement not met: " + text[:120]
        if menu:
            # menu items look like " ~ Swordsman"; strip the bullet, match loosely
            clean = [m.replace("\u00a0", " ").split(" ~ ")[-1].strip() for m in menu]
            want = next((i for i, c in enumerate(clean)
                         if c.lower() == target_job_name.lower()), None)
            if want is None:  # substring fallback (e.g. 'Swordman' vs 'Swordsman')
                want = next((i for i, c in enumerate(clean)
                             if target_job_name.lower() in c.lower()
                             or c.lower() in target_job_name.lower()), None)
            if want is None:
                return False, "job_change: '%s' not in menu %s" % (target_job_name, menu)
            log("  JOB menu: choosing '%s' (option %d)" % (menu[want], want + 1))
            dlg = choose(api, want + 1)
            continue
        # Stale dialog: menu present but empty (idle >~10s shows only close). Re-open fast.
        if dlg.get("open") and not menu and not dlg.get("next") and dlg.get("close"):
            log("  JOB dialog went stale; reopening")
            api("close")
            time.sleep(0.8)
            dlg = interact_npc(api, "Job Master", log=log)
            continue
        # confirmation dialog: 'Do you want to change into X class?'
        if "want to change" in text.lower() or "change into" in text.lower():
            # option 1 = Change into X (the confirm menu has 'Change' first)
            log("  JOB confirm: yes")
            dlg = choose(api, 1)
            # after Job_Change the dialog closes
            time.sleep(1.5)
            return True, "job_change: changed to %s" % target_job_name
        # 'You are now ...' success text -> close
        if "you are now" in text.lower():
            close(api)
            return True, "job_change: done"
        # plain text, advance
        nxt = dlg.get("next")
        if nxt:
            dlg = dialog_next(api)
            continue
        # nothing actionable
        break
    close(api)
    return False, "job_change: dialog exhausted without completing (last text: %s)" % (
        (dlg.get("text") or "")[:120],)
