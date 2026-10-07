#!/usr/bin/env python
# NPC interactions: Job Master (job change) and any dialog-driven NPC.
# Uses the game API's walk/interact/dialog/next/choose — the same flow a human
# clicks through. Job Master script (from standart-npc mod) needs all skill
# points spent FIRST (Check_SkillPoints), then a menu -> confirm dialog.
import time

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

def next(api):
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


def find_npc(api, name, center=None, scan=60, step=8):
    """Locate an NPC by hovering pixels around `center` (default: its reported
    click point) until the cursor picks a sprite whose name starts with `name`.
    NPCs often overlap; the reported click point can hit a neighbor. Returns
    (x, y) pixels of the sprite, or None."""
    # find the entity's reported click point as a starting guess
    st = api("state", ["20"]) or {}
    ent = next((e for e in st.get("entities", [])
                if e.get("type") == "NPC" and e.get("name", "").startswith(name)), None)
    if not ent and not center:
        return None
    cx, cy = center or (ent.get("click", {}).get("x"), ent.get("click", {}).get("y"))
    if cx is None:
        return None
    # scan a spiral of offsets around the guess
    for r in range(0, scan, step):
        for dx in range(-r, r + 1, step):
            for dy in range(-r, r + 1, step):
                if max(abs(dx), abs(dy)) != r:
                    continue
                h = api("hover", [str(cx + dx), str(cy + dy), "--px"])
                over = (h.get("mouse") or {}).get("over") or {}
                if over.get("name", "").startswith(name):
                    return (cx + dx, cy + dy)
    return None

def interact_npc(api, name, log=print):
    """Walk to an NPC's area, find its true sprite pixels, and interact (click)."""
    st = api("state", ["20"]) or {}
    ent = next((e for e in st.get("entities", [])
                if e.get("type") == "NPC" and e.get("name", "").startswith(name)), None)
    if not ent:
        return {"ok": False, "reason": "npc '%s' not found nearby" % name}
    # approach the NPC's cell (walk stops adjacent)
    x, y = ent.get("position", [0, 0])
    api("walk", [str(x), str(y)])
    time.sleep(1.2)
    px = find_npc(api, name)
    if not px:
        return {"ok": False, "reason": "found %s but could not hover its sprite" % name}
    log("  NPC %s at pixels %s" % (name, px))
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
            # pick the target job from the menu
            want = next((i for i, m in enumerate(menu) if m.strip().lower() == target_job_name.lower()), None)
            if want is None:
                return False, "job_change: '%s' not in menu %s" % (target_job_name, menu)
            log("  JOB menu: choosing '%s' (option %d)" % (menu[want], want + 1))
            dlg = choose(api, want + 1)
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
            dlg = next(api)
            continue
        # nothing actionable
        break
    close(api)
    return False, "job_change: dialog exhausted without completing (last text: %s)" % (
        (dlg.get("text") or "")[:120],)
