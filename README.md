# Laya Ragnarok Agent

A **local, Laya-driven autoplay agent** for [Ragnarok Offline](https://github.com/Flux159/ragnarokoffline.app).
It splits the work like a human player's brain and reflexes:

- **Hermes (the brain)** writes a plain-text `goal.json` — *what* to do
  ("farm safely on `prt_fild08`, potion under 50% HP, flee if overwhelmed").
- **Laya** (a non-autoregressive System-1 decision model, ~33 ms per call, runs
  fully offline) reads the game state and picks the next action — *how* to react.
- **This agent** executes the chosen action against the game's local HTTP API
  (or the DOM, via the `js` command) — *doing* it.

The point is token economy: the LLM sets the goal once, then Laya plays the
per-tick decisions locally for free. Hermes only steps in when the agent is
stuck or a player talks to it.

```
 goal.json  ──►  agent.py  ──►  Laya  ──►  action  ──►  game (HTTP API / DOM)
   (Hermes)      (orchestrator)  (decide)   (combat,    localhost:PORT/v1/command
                                            skills, NPC,
                                            inventory,
                                            shops)
```

## How it works

`agent.py` runs a loop (`--steps N`, or forever):

1. **Read state** — `dbread.py` snapshots the character (HP/SP, stats, zeny,
   inventory, equipment, nearby mobs) from the live game DB, plus the game API's
   `state`/`js` reads for anything that lags the DB (weight, skill points,
   shop windows).
2. **Ask Laya** — the state + `goal.json` rules become typed questions
   (choice / score / noul with calibrated probabilities); Laya returns an
   action in a single forward pass.
3. **Execute** — `combat.py` runs it (`attack`, `flee`, `heal`, `wander`,
   `greet`, `idle`, `allocate`), `game_api.py` recovers from death by finding
   the "Return to last save point" button in a screenshot.
4. **Repeat.**

Everything the agent *can do* lives in small sibling modules:

| Module | What it does |
|---|---|
| `core.py` | shared `log`, `api`, `load_goal`, `dist` — avoids circular imports |
| `dbread.py` | live DB snapshot (char, inventory, equipment, mobs) for Laya's state |
| `combat.py` | execute the Laya-picked action |
| `game_api.py` | death recovery (screenshot → pixel-find respawn button → click) |
| `knight_build.py` | stat read + Knight build plan + spend points |
| `skill_ops.py` | spend skill points toward a class preset (novice, knight, …) |
| `inventory_ops.py` | weight, equip/unequip, use consumables, read inventory via DOM |
| `npc_ops.py` | Job Master dialog flow, player-shop discovery (`find_shops`) |
| `js_ops.py` | run page JS through the agent's `js` command (DOM reads) |
| `operations.py` | high-level op sequences (train skills → job change → inventory → sell) |

### Reading the game the fast way

The old approach pixel-guessed the screen (hover-spiral scans, vision coordinate
guesses). It now reads the **DOM** through the `js` command the Ragnarok Offline
agent API exposes — shadow-DOM aware helpers (`__deep`, `__visible`, `__rect`) plus
`window.roAgent` give exact element positions:

```python
import npc_ops
npc_ops.find_shops(api)        # 28 shop signs in 0.01 s (was 155 s of hovering)
npc_ops.shops_of_kind(api, "buy")   # buying stores only
npc_ops.shop_by_text(api, "Novice Armlet")
```

`store_items` then reads an open shop's rows — **name, quantity, price** — for
both sell (`S>`) and buy (`B>`) vendors, ready to dump to JSON.

## Setup

1. **Ragnarok Offline** running with the agent API enabled
   (`%APPDATA%\Ragnarok Offline\state\agent\connection.json` holds the port +
   bearer token).
2. **Laya installed** in a venv, e.g.:

   ```bash
   pip install laya   # or: uv pip install laya
   ```

3. **Python deps**: `Pillow` (death-recovery screenshots). The DB reads use the
   game's MariaDB (`ragnarok/ragnarok` guest creds).

```bash
python agent.py --steps 50      # run 50 ticks
python agent.py                 # run forever
```

## The goal file

`goal.json` is the only thing Hermes edits to steer the agent:

```json
{
  "goal": "Fight monsters on prt_fild08/f00 safely. Drink a potion when HP drops below 50 percent. Flee if overwhelmed.",
  "rules": { "attack": true, "greet": false, "heal": true, "build": true },
  "greetings": ["o/"]
}
```

`rules` gates which actions Laya may pick; `build` enables stat/skill allocation
toward the preset in `knight_build.py` / `skill_ops.py`.

## Status

Beta / research. The decision loop, stat & skill allocation, Job Master job
changes, inventory/weight handling, and player-shop discovery work against the
live game. Shop **contents** (name / qty / price → JSON) are wired but need the
`clicksel` build installed to open vendor windows.

## Credits

- Decision model: [Laya](https://huggingface.co/convaiinnovations/laya) by
  convaiinnovations (Apache-2.0).
- Game: [Ragnarok Offline](https://github.com/Flux159/ragnarokoffline.app)
  (fork: `clarois/ragnarokoffline.app`), roBrowserLegacy client, rAthena server,
  population-engine for the simulated players/NPC vendors.
