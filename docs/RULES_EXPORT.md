# FM Bench — Rules & Mechanics Export (for peer review)

This document is a peer-facing export of what an LLM agent actually receives and can do inside FM Bench, plus the win/scoring and numeric mechanics, laid out verbatim so reviewers can compare against their own agent benchmarks.

The game itself is a football-club-manager simulation with the following locked product modes (names finalized): **Solo**, **Arena**, and **Open Track**. The simulated world is a single division of **16 clubs**, with up to **15 scripted opponents** as calibration baselines.

The document contains, in order:
1. **Overview** — what the agent is playing.
2. **System prompt** — the complete verbatim system prompt the agent receives (a real 9701-character engine artifact).
3. **Stop packet** — real per-turn `stop packet` dumps (seed=1, 5-year track), not hand-written.
4. **Tool returns** — real observation-layer query outputs the agent sees (seed=1).
5. **Tools** — the 26 tools the agent can call, with their exact JSON schemas.
6. **Win/scoring mechanics** — how a run ends and how the score is computed.
7. **Numeric mechanics** — annotated `params.yaml` (the single source of truth).
8. **Determinism & fairness** — reproducibility and human/agent parity.

Everything shown as **prompt / packet / tool-return / schema / params** is a **real engine artifact or verbatim source** (produced with seed=1 on the 5-year track), not an illustrative mock-up. These verbatim blocks are language-neutral and are shown once, in the English half of this document, exactly as the engine emits them — they are deliberately NOT duplicated into the Chinese half.

---

> This file is for peers building similar **agent-benchmark games**. The goal is to lay out verbatim **what the agent actually receives** (the system prompt, each turn's context / stop packet, the callable tools) plus the **win and numeric mechanics**, so they can be compared side by side.
>
> Every **prompt / packet / tool-return / schema / param** in this file is a **real engine output or verbatim source** (produced with seed=1 on the 5-year track), not a hand-written illustration. The prose is bilingual (full English version on top, full Chinese version below); the code / JSON / params stay in their original English form and appear once.

---

## Table of contents

1. [Overview: what the agent is actually playing](#1-overview)
2. [The system prompt the agent receives (verbatim)](#2-system-prompt)
3. [Per-turn context: a real stop packet (raw dump)](#3-stop-packet)
4. [Tool returns the agent sees (raw dump)](#4-tool-returns)
5. [Tools the agent can call (26, with schemas)](#5-tools)
6. [Win/scoring mechanics: how a run ends and how the score is computed](#6-winscoring-mechanics)
7. [Numeric mechanics: the full annotated params.yaml](#7-numeric-mechanics)
8. [Determinism & fairness](#8-determinism--fairness)

---

## 1. Overview

FM Bench is a "football-club manager" simulation used to test an LLM agent's **long-horizon management-decision intelligence**. The agent runs a fictional club for a fixed N game-years (typically 5 or 20). It controls everything except the players on the pitch (matches are simulated by the engine from squad/tactics): transfers, contracts, tactics, youth, facilities, finances. The run is scored at the end by a single composite score.

The interaction is **event-driven turn-based**. The engine advances day by day and pauses whenever it needs the agent to decide something (transfer window, an offer, an injury, monthly report, board warning, season settlement, ...), packing a **stop packet** and sending it to the agent. Each stop is a **fresh conversation** (no accumulated history): the runner sends `system prompt (fixed) + this turn's stop packet`, and within that stop the agent runs a **multi-round tool-calling loop** (query → act → query again ...), finally calling `advance` to end the turn and advance time. In practice a 5-year track is about 85–90 stops, each about 5–12 API calls.

**No conversation memory carries across stops.** The agent's continuity relies on only two things: (1) the engine's **read-only archive** (`get_history`, match reports, finances, etc. — objective facts queryable at any time); (2) the agent's own **notebook** (auto-attached to every stop packet, with a hard capacity cap). "Whether it leaves useful notes for its future self" is itself one of the tested abilities.

---

## 2. System prompt

How the runner assembles it (`runner/render.py`): `system = ROLE_PREAMBLE + "\n\n---\n\n" + rules.md`. This preamble + rulebook is **frozen byte-for-byte** (no timestamp, does not vary by stop) so it hits the prompt cache. Below is the **complete verbatim** system prompt the agent receives (9701 characters):

````text
You are playing FM Bench as the manager of your club. The full game manual follows. Play to maximize your final composite score over the whole run. At each decision stop: read the packet, query what you need, act, keep your notebook current, then call `advance` to end the stop. Each stop is a fresh conversation — your notebook and the game archive are your only memory.

---

# FM Bench — Game Manual

You are the manager of a professional football club in a fictional world. You
control everything about the club except the ball: transfers, contracts,
tactics, youth development, facilities and finances. You never control players
during a match — the match engine plays it out from the squad, lineup and
tactics you chose.

The run lasts a fixed number of in-game years (shown in every stop packet as
`year_of_run`). Your objective is to maximize your final composite score.

## 1. Score

Three channels, each compressed (diminishing returns) and weighted, then summed:

- **Honors** — trophy points earned over the whole run: league titles,
  top-4 finishes, promotion. Relegation *subtracts* points. Second-division
  honors are worth much less than first-division ones.
- **Net worth** — the club's financial position at the end of the run
  (averaged over the final seasons, so last-minute window dressing does not
  work). Hoarded cash far beyond your wage bill is discounted.
- **Squad value** — the real market value of your squad at the end of the run
  (also averaged over the final seasons).

Notes:
- Because each channel has diminishing returns, a balanced club outscores a
  one-dimensional one.
- If you are **fired** (or abandon the run), the run settles immediately and
  your score is multiplied by an early-settlement factor that grows with how
  much of the run you completed. Being fired early is catastrophic for score.
- There are no points for style, effort or process. Only outcomes count.

## 2. The world

- Two divisions of 16 clubs each. Double round-robin (30 rounds), one match
  roughly every week of the season. Top clubs earn far more revenue than
  small ones.
- Promotion and relegation: 3 clubs each season.
- The season runs on a fixed yearly calendar: summer transfer window →
  league season (with a winter transfer window mid-season) → season
  settlement → youth intake. Then the next year begins.
- Every other club is run by its own AI management: they buy, sell, renew,
  change tactics and react to results — and they operate under the same
  uncertainty you do.

## 3. How you play: decision stops

Time simulates day by day on its own. It pauses at **decision stops** and
hands you a *stop packet*:

- `date`, `stop_types` — why the game stopped (season plan, transfer
  deadline, monthly report, incoming offer batch, season settlement, ...).
- `digest` — one-glance club state: division, league position, points, cash,
  wage ratio, board confidence, season target, injuries, next fixture, open
  window, plus `warnings[]` you should take seriously.
- `inbox` / `action_required` — events since the last stop; some need answers.
- `pending_offers` — live transfer negotiations involving your club.
- `recent_actions` — your last few actions, for continuity.
- `notebook` — your own persistent notes (see §7).

At a stop you may call query tools to inspect anything, then action tools to
decide, in any order. When you are done, call **`advance`** — the only way to
move time forward. Whatever you did not decide gets default/conservative
handling. Offers expire if ignored.

There is a **query budget per stop** and a separate **negotiation-move budget
per stop** (the packet digest plus your notebook are free). Both reset every
time you `advance`. Spend queries where the decision is actually close.
Negotiation etiquette: hammering the same player or seller with rapid repeat
offers triggers cooldowns and rising demands — space your approaches out.

## 4. Squad, matches and tactics

- You field 11 starters. Set a preferred lineup with `set_lineup`; injured or
  missing players are auto-replaced by the engine's best guess.
- `set_tactics` picks a formation and a playing style (enums). Styles interact
  rock-paper-scissors-like: no style dominates, and any edge is a modest
  modifier, not a win button. Switching style takes a little time to bed in.
- Squad strength, form, fatigue and morale drive results far more than
  tactical tinkering. Rotate: tired players perform worse and get injured
  more.
- Players develop with age, playing time and training facilities; young
  players grow, older players decline — steeply after their peak. Peak age
  differs by position. Injuries happen; severe ones can permanently reduce a
  player.

## 5. Transfers, contracts and information

- Windows: summer and winter only. Buy with `make_transfer_offer` (negotiation
  resolves in-stop: the seller accepts, rejects or counters — respond with
  `respond_to_offer`). Sell by listing players (`list_player`) and answering
  incoming offers. Sign free agents and renew your own players with
  `offer_contract` (players counter with their wage demands). When you raise
  an offer or accept a counter-wage, `years` may be omitted — it defaults to
  the term of your previous offer to that player. Free agents also demand a
  one-off signing fee (shown in the transfer market view) on top of wages.
- Contracts matter: a player whose contract runs out leaves for free. Renew
  early or sell — expiring players lose transfer value.
- **All ability information is uncertain.** You never see true ability — only
  coarse ranges with a confidence letter (own players are known best, players
  in your division somewhat, everyone else poorly). Scout reports are noisy
  AND systematically biased: **scouting the same player repeatedly does NOT
  converge to the truth** — it converges to the truth *plus that scout
  network's bias*. Cross-check with actual match output (goals, ratings,
  minutes) and be skeptical of your own certainty. Finding systematically
  underpriced players is where great managers make their profit.
- Youth academy: prospects appear at the yearly intake, rated in potential
  stars (also uncertain). `promote_youth` moves one to the senior squad.
  Academy and training investments (`invest`) pay off over years, not months.
- Sell-side prices react to you: flipping players quickly and farming the
  same market repeatedly gets priced in. Every deal carries transaction
  friction.

## 6. Finances, the board, and getting fired

- Revenue: gate receipts, TV/prize money by division and final position,
  commercial income. Costs: wages (the big one), transfer spending, facility
  projects, severance. Division and league position drive revenue enormously
  — relegation roughly halves your income.
- Keep the wage bill a sustainable fraction of revenue. You will be warned
  (`wage_ratio_critical`) before it becomes fatal. Run out of cash and the
  club enters administration — a deep hole with lasting consequences.
- The **board** holds a confidence meter, updated by results vs expectations
  (they judge your *full squad's* strength, so fielding weak lineups to lower
  expectations does not work), league position vs the season target, and
  financial discipline. Chronic under-investment in the squad also angers
  them. Low confidence triggers a firing vote; very low confidence is instant
  dismissal. High confidence earns budget boosts.
- On day one the board sends a **briefing** stating where your finances stand
  relative to their comfort line — read it; your starting wage bill may
  already be close to the line.
- The board judges trends, not just levels: visibly *fixing* a finance
  problem softens their monthly verdicts, and a top-half league position puts
  a floor under their patience. Digging out of a bad season is meant to be
  hard but possible.
- Getting fired ends the run with the early-settlement penalty (§1). Watch
  `board_confidence` in every digest.

## 7. Memory: your notebook

Each decision stop is handled fresh — the only things that persist for you
are the game's queryable archive (`get_history`, match reports, finances) and
your **notebook**, which is auto-shown in every stop packet.

- `append_note` adds text; `rewrite_notes` replaces everything. Capacity is
  hard-limited (a few thousand words) — curate it, don't hoard it.
- Facts you can re-query do not belong in the notebook. Judgments do: your
  multi-year plan, scouting suspicions, lessons from failures, negotiation
  reads, what you promised the board.
- Write for your future self who remembers nothing else.

## 8. Standing orders

`set_standing_order` automates simple reactions between stops (e.g.
auto-reject lowball offers below a multiple of a player's perceived value).
They are deliberately limited: a small fixed set of order types, single
threshold each, hard cap on count. They reduce interruptions; they cannot
play the game for you.

## 9. Tool quick reference

Queries (read-only, budgeted per stop):
`get_club_overview`, `get_squad`, `get_player`, `get_league_table`,
`get_fixtures`, `get_match_report`, `get_transfer_market`, `get_finances`,
`get_youth_academy`, `get_history`, `get_inbox`.

Actions:
`set_lineup`, `set_tactics`, `make_transfer_offer`, `respond_to_offer`,
`offer_contract`, `list_player`, `release_player`, `promote_youth`,
`invest`, `set_standing_order`.

Notebook: `append_note`, `rewrite_notes`. Control: `advance`.

Illegal actions return an error with a reason code and cost you nothing —
but they also do nothing. Exact balance numbers (pricing formulas, noise
magnitudes, board thresholds) are intentionally not disclosed; learning the
world empirically is part of the game.
````

---

## 3. Stop packet

At each stop the runner renders the packet into one user message: `"DECISION STOP {stop_id}\n" + json.dumps(packet, indent=1, sort_keys=True)` (`runner/render.py: render_packet`). Every field of the packet is filtered through the observation layer (`engine/sim/stops.py: build_packet`) and **contains no true hidden values**.

Packet field meanings: `digest` (one-glance club state: position / cash / wage ratio / board confidence / season target / injuries / next fixture / whether a window is open / `warnings[]`); `inbox` + `action_required` (events since the last stop, some needing a response); `pending_offers` (in-progress transfer negotiations); `recent_actions` (recent actions, for continuity); `notebook` (the agent's own notes).

The two below are **real engine outputs** (seed=1, 5-year track, not hand-written).

### 3.1 The opening decision stop (S0001: preseason board + contract-expiry reminders)

Note the `board_briefing` in the `inbox`: this is the opening board briefing, which explicitly tells the agent "your wage bill is already close to the board's comfort line, and season expectations are anchored to the squad you inherit — selling the squad down will not lower the bar you are judged against" (guards against "shrink-to-sandbag").

````json
DECISION STOP S0001
{
 "action_required": [],
 "date": {
  "day": 2,
  "month": 1,
  "year": 1
 },
 "digest": {
  "board_confidence": 50,
  "burn_trend": "too early to read a monthly trend",
  "cash_m": 37.2,
  "division": 1,
  "injured_players": 0,
  "league_position": 7,
  "next_fixture": {
   "day": 65,
   "home": true,
   "opponent": "Torton Athletic",
   "round": 1
  },
  "points": 0,
  "season_target": 6,
  "squad_size": 25,
  "wage_ratio": 0.744,
  "warnings": [],
  "window": "summer",
  "year_of_run": "1/5"
 },
 "inbox": [
  {
   "action_required": false,
   "day": 1,
   "payload": {
    "detail": "near_line",
    "kind": "finance",
    "text": "Your wage bill is close to the board's comfort line. There is little headroom for new wages until revenue grows. The board values financial discipline as much as results; sustained overspending erodes its confidence. Note that expectations are anchored to the squad you inherit each season: selling the squad down will not lower the bar you are judged against this year."
   },
   "type": "board_briefing",
   "year": 1
  },
  {
   "action_required": false,
   "day": 2,
   "payload": {
    "months_left": 9,
    "player": "P00178"
   },
   "type": "contract_expiring",
   "year": 1
  },
  {
   "action_required": false,
   "day": 2,
   "payload": {
    "months_left": 9,
    "player": "P00181"
   },
   "type": "contract_expiring",
   "year": 1
  },
  {
   "action_required": false,
   "day": 2,
   "payload": {
    "months_left": 9,
    "player": "P00187"
   },
   "type": "contract_expiring",
   "year": 1
  }
 ],
 "notebook": "",
 "pending_offers": [],
 "recent_actions": [],
 "stop_id": "S0001",
 "stop_types": [
  "PRESEASON_BOARD",
  "CONTRACT_EXPIRY"
 ]
}
````

### 3.2 Summer transfer-window planning stop (S0002: WINDOW_SUMMER_PLAN)

````json
DECISION STOP S0002
{
 "action_required": [],
 "date": {
  "day": 5,
  "month": 1,
  "year": 1
 },
 "digest": {
  "board_confidence": 50,
  "burn_trend": "too early to read a monthly trend",
  "cash_m": 36.0,
  "division": 1,
  "injured_players": 0,
  "league_position": 7,
  "next_fixture": {
   "day": 65,
   "home": true,
   "opponent": "Torton Athletic",
   "round": 1
  },
  "points": 0,
  "season_target": 6,
  "squad_size": 25,
  "wage_ratio": 0.744,
  "warnings": [],
  "window": "summer",
  "year_of_run": "1/5"
 },
 "inbox": [],
 "notebook": "",
 "pending_offers": [],
 "recent_actions": [],
 "stop_id": "S0002",
 "stop_types": [
  "WINDOW_SUMMER_PLAN"
 ]
}
````

---

## 4. Tool returns

What the agent sees when it calls a query tool inside a stop is also the observation-layer filtered output. **Key point: ability is always a range + a confidence letter, never a point estimate.** Below is a real dump (seed=1):

- Own-squad players: `confidence: "A"`, band width 8 (e.g. 121–129);
- Players outside your club: `confidence: "B"` (band width 16) or `"C"` (band width 30, e.g. 85–115) — the less you know, the wider it gets.

Repeatedly scouting the same player **does not converge to the truth**; it converges to "the truth + that scout network's built-in bias" (see the §7 scouting params).

### 4.1 `get_squad` (top 2 of 25 — real output)

````json
{
 "data": [
  {
   "ability": {
    "ca_high": 129,
    "ca_low": 121,
    "confidence": "A"
   },
   "age": 27,
   "club": "Linford Athletic",
   "club_id": "C00008",
   "contract_months_left": 33,
   "fatigue": "fresh",
   "form": 6.5,
   "injury_days": 0,
   "listed": false,
   "morale": "ok",
   "name": "As Sorholm",
   "nationality": "Noremic",
   "pid": "P00198",
   "position": "ST",
   "season_stats": {
    "apps": 0,
    "assists": 0,
    "goals": 0,
    "minutes": 0
   },
   "wage_m_per_year": 4.7,
   "youth": false
  },
  {
   "ability": {
    "ca_high": 122,
    "ca_low": 114,
    "confidence": "A"
   },
   "age": 22,
   "club": "Linford Athletic",
   "club_id": "C00008",
   "contract_months_left": 45,
   "fatigue": "fresh",
   "form": 6.5,
   "injury_days": 0,
   "listed": false,
   "morale": "ok",
   "name": "Del Santiago",
   "nationality": "Valdorian",
   "pid": "P00184",
   "position": "FB",
   "season_stats": {
    "apps": 0,
    "assists": 0,
    "goals": 0,
    "minutes": 0
   },
   "wage_m_per_year": 4.0,
   "youth": false
  }
 ],
 "ok": true
}
````

### 4.2 `get_transfer_market` (2 entries excerpted — real output)

Note both are `status: "expiring"` (9 months left on contract), one band B and one band C — market players carry clearly more uncertainty than your own squad.

````json
{
 "data": [
  {
   "ability": {
    "ca_high": 143,
    "ca_low": 127,
    "confidence": "B"
   },
   "age": 24,
   "club": "Velmere City",
   "club_id": "C00011",
   "contract_months_left": 9,
   "form": 6.5,
   "injury_days": 0,
   "listed": false,
   "name": "Al Alworth",
   "nationality": "Kestrelian",
   "pid": "P00257",
   "position": "CB",
   "season_stats": {
    "apps": 0,
    "assists": 0,
    "goals": 0,
    "minutes": 0
   },
   "status": "expiring",
   "youth": false
  },
  {
   "ability": {
    "ca_high": 115,
    "ca_low": 85,
    "confidence": "C"
   },
   "age": 26,
   "club": "Falport Wanderers",
   "club_id": "C00021",
   "contract_months_left": 9,
   "form": 6.5,
   "injury_days": 0,
   "listed": false,
   "name": "Al Brenworth",
   "nationality": "Kestrelian",
   "pid": "P00518",
   "position": "W",
   "season_stats": {
    "apps": 0,
    "assists": 0,
    "goals": 0,
    "minutes": 0
   },
   "status": "expiring",
   "youth": false
  }
 ],
 "ok": true
}
````

An illegal call returns `{"ok": false, "error": {"code": ..., "hint": ...}}`, which **costs no points and does not change the world**; the hint contains no exact numbers (to prevent reverse-engineering hidden values from errors).

---

## 5. Tools

The agent can call **26 tools** (`engine/actions/commands.py`) — 24 always live, plus 2 draft-phase tools that return `no_draft` unless the opt-in draft is enabled (`params.draft.enabled`, default `false`). Each is exposed to the model as a strict JSON schema. The tables below are grouped by category (`*` marks required parameters):

### Query (read-only, budget 30 per stop)

| tool | params | description |
|---|---|---|
| `get_club_overview` | — | Your club: division, cash, board, facilities, tactics. |
| `get_finances` | — | Your cash, revenue, wage bill, warnings. |
| `get_fixtures` | — | Your season fixtures and results. |
| `get_history` | topic*:enum['honors', 'seasons', 'transfers'], year:integer | Archive: honors / transfers / seasons. |
| `get_inbox` | — | Pending offers and open items. |
| `get_league_table` | division:integer | League standings. |
| `get_match_report` | match_id*:string | Match report by match id. |
| `get_player` | player_id*:string | Detailed view of any player by id. |
| `get_squad` | — | Your senior squad with ability bands, form, contracts, wages. |
| `get_transfer_market` | — | Players available to buy: listed, free agents, expiring contracts. |
| `get_youth_academy` | — | Your academy prospects with potential stars. |

### Action (negotiation actions budget 10 per stop)

| tool | params | description |
|---|---|---|
| `invest` | kind*:enum['academy', 'stadium', 'training'] | Start a facility upgrade: academy, training or stadium expansion. |
| `list_player` | listed*:boolean, player_id*:string | Put a player on / off the transfer list. |
| `make_transfer_offer` | amount_m*:number, player_id*:string | Bid for another club's player (negotiation resolves within this stop). |
| `offer_contract` | player_id*:string, wage_m_per_year*:number, years:integer | Offer a contract: renew own player, or sign a free agent. 'years' is optional on follow-up offers: it defaults to the term of your previous offer to this player (so accepting a counter-wage only needs player_id and the wage). |
| `promote_youth` | player_id*:string | Promote an academy prospect to the senior squad. |
| `release_player` | player_id*:string | Terminate a contract (severance: half of one year's wage). |
| `respond_to_offer` | action*:enum['accept', 'accept_counter', 'counter', 'reject', 'withdraw'], counter_amount_m:number, offer_id*:string | Respond to a pending offer: accept / reject / counter / accept_counter. |
| `set_lineup` | player_ids*:array | Set your preferred starting XI (auto-completed if players unavailable). |
| `set_standing_order` | mult:number, order_type*:enum['accept_offers_above', 'clear_all', 'reject_offers_below'] | Add or clear a standing order (max 10; single threshold + fixed action). |
| `set_tactics` | formation:enum['3-5-2', '4-3-3', '4-4-2'], style:enum['gegenpress', 'lowblock', 'possession'] | Set formation and playing style (enums only). |

### Note

| tool | params | description |
|---|---|---|
| `append_note` | text*:string | Append to your private notebook (8K token cap). |
| `rewrite_notes` | text*:string | Replace the entire notebook. |

### Draft (opt-in phase only; `params.draft.enabled`, default `false`)

Both tools are always present in the tool list, and both return
`{"ok": false, "error": {"code": "no_draft"}}` when no draft phase is in
progress — so the tool list stays byte-identical (and prompt-cacheable)
whether or not the world uses a draft.

| tool | params | description |
|---|---|---|
| `get_draft_pool` | — | Draft phase only: the shared template pool — position, age, your scout's ability band, potential stars, price, preset wage and contract length — plus your budget status. |
| `submit_draft` | picks*:array[string], auto_fill:boolean | Draft phase only: submit your complete pick list of template ids. A valid list has >= min_picks players including a goalkeeper and fits the budget; an invalid list is rejected with the reason. Resubmitting replaces your previous list. Set auto_fill=true to have an invalid/short list completed from the cheapest tier. |

### Control

| tool | params | description |
|---|---|---|
| `advance` | — | End this decision stop; simulate until the next stop. Returns the next stop packet. |

**Budgets**: queries 30 per stop, negotiation (`make_transfer_offer` + `respond_to_offer`) 10 per stop, both reset after `advance`. `digest` and `notebook` are free (bundled into every packet).

A full schema looks like this (`make_transfer_offer`, the function definition the model actually sees):

````json
{
 "name": "make_transfer_offer",
 "description": "Bid for another club's player (negotiation resolves within this stop).",
 "input_schema": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
   "player_id": {"type": "string"},
   "amount_m": {"type": "number", "minimum": 0}
  },
  "required": ["player_id", "amount_m"]
 }
}
````

---

## 6. Win/scoring mechanics

### 6.1 How a run ends (three settlement paths)

A run ends under any of the following, recording a `settle_reason`:

| outcome | trigger | consequence |
|---|---|---|
| `completed` | ran the full configured N years | no discount (rho = 1.0) |
| `fired` | board confidence too low (see §7 board) | immediate settlement + rho discount (the earlier you are fired, the worse) |
| `administration` | cash below "annual revenue × −10%" for 90 consecutive days (`admin_days: 90`, `admin_cash_threshold: -0.10`) | immediate settlement + rho discount + point / league-point penalty |

Fired / administration / abandoning mid-run all go through **early settlement**: the positive part of the score is multiplied by `rho(t)` (a power function of completion), the negative part is not discounted.

### 6.2 Score formula (score_v0.2, verbatim from `score/composite.py`)

```
S_raw = wh * ln(1 + H/h_div)                          # honors channel
      + ww * sign(VA) * ln(1 + |VA|/va_w_div)         # net-worth "increment" channel
      + wm * ln(1 + max(M,0)/m_div)                   # squad-value channel (stabilizer)
S_final = rho(t) * max(S_raw, 0) + min(S_raw, 0)

where VA = (deflated mean net worth over the last 3 seasons) − (day-0 net worth of the same-seed world)
      rho = 1.0                            if completed
          = rho_base * (t/years)^rho_exp   otherwise (early settlement)
```

The three channels are each **log-compressed** (diminishing returns, forcing balanced club-building) then weighted and summed:

- **Honors** — cumulative trophy points over the whole run (league title / top-4 / promotion; relegation subtracts; second-division weights far below first).
- **Net worth — records the "increment VA", not the balance**: the deflated last-3-season net-worth mean **minus day-0 net worth**. **Zero = "you handed the club back as valuable as you found it"**; only a gain is positive. Net worth = cash + discounted squad + facilities − unamortized transfer fees; buying a player books both the asset and the cash/fee side, so "buying spree" cannot pump the score.
- **Squad value** — last-3-season mean (guards against pre-settlement pump), lower weight, purely a stabilizer.

**Why "increment" rather than "absolute"** (round-4 calibration; the round-by-round log is an internal record, not part of this release): v0.1 used absolute wealth, which let "starting-endowment luck" explain 34% of score variance and skill only 16%; subtracting one's own starting endowment flipped it to 13% / 33% and fixed the ranking inversion on hard seeds. `rho` multiplies only the positive part — so "get fired early to cut losses" does not hold (no arbitrage). Scores are only comparable **within the same track length** (the rho base varies with the number of years).

---

## 7. Numeric mechanics

Below are the key blocks of `params.yaml` (the single source of truth), **verbatim + annotations**. The rulebook **hides** these constants from the agent ("empirically learning the world is part of the game"); they are disclosed to peers here at the owner's request.

### 7.1 Scoring weights `score`

```yaml
score:
  w_honors: 18            # honors channel weight
  w_networth: 10          # net-worth (increment) channel weight
  w_squadvalue: 6         # squad-value weight (lowered to a stabilizer in v0.2)
  h_div: 60               # honors log divisor
  va_w_div: 40            # net-worth increment log divisor
  m_div: 80               # squad-value log divisor
  honors:                 # honor points per trophy
    T1_CHAMPION: 100      # first-division champion
    T1_TOP4: 20           # first-division top-4
    T2_CHAMPION: 30       # second-division champion
    T2_PROMOTED: 15       # promotion
    RELEGATED_T1: -40     # relegated from first division (penalty)
  last_n_seasons: 3       # net worth / squad value use the last-3-season mean
  rho_base: 0.8           # early-settlement discount base
  rho_exp: 1.5            # early-settlement discount exponent
```

### 7.2 Board & firing `board`

Board confidence is the "survival currency". It is driven by results vs expectations, position vs season target, and financial discipline. Expectations are set **by full-squad strength** (guards against fielding weak lineups to lower expectations).

```yaml
board:
  start_confidence: 50
  upset_win: 6            # upset win +6
  expected_win: 3
  expected_loss: -3
  upset_loss: -6
  upset_margin: 0.25      # win probability below this counts as an upset
  draw_as_favourite: -3
  draw_as_underdog: 3
  monthly_per_place: -1   # monthly penalty per place behind target
  season_gap_cap: 12
  finance_penalty: -3     # monthly penalty when wage ratio breaks the warning line
  finance_improving_factor: 0.5  # if wage ratio is falling, halve the penalty (rewards "fixing it")
  upper_half_conf_floor: 25       # top-half position puts a floor under confidence
  y1_monthly_factor: 0.5          # year-1 monthly penalty halved (rookie protection)
  fire_vote_below: 20     # confidence < 20 triggers a firing vote
  fire_vote_prob: 0.6     # firing probability given a vote
  fire_below: 10          # confidence < 10 is instant dismissal
  budget_boost_above: 80  # confidence > 80 grants a transfer-budget boost
  patience_min: 3         # grace months in the vote zone (lower bound)
  patience_max: 5
  strength_floor_decay: 0.5       # season-expectation floor decays toward the real squad (allows a 1-2 year rebuild)
  strength_floor_scale_cap: 1.30
```

### 7.3 Match simulation `match` + tactics `tactics`

The scoreline = segmented Poisson (`base_lambda` × home advantage × strength-ratio^alpha × tactics/form modifiers). Tactics are rock-paper-scissors, but their total effect is capped at ±0.20 and is **not a win button**.

```yaml
match:
  base_lambda_home: 1.45  # home base goal rate
  base_lambda_away: 1.20
  home_adv: 1.18          # home advantage
  alpha: 1.35             # strength-gap -> result sensitivity exponent (higher = strong teams dominate more)
  segments: 6             # a match is simulated in 6 segments
  max_goals: 9
  matchup_cap: 0.20       # total cap on all tactics/style modifiers, ±20%
  style_switch_penalty_matches: 2  # a style switch needs 2 matches to bed in
  form_mult_min: 0.92     # form-to-performance multiplier range
  form_mult_max: 1.08
  rating_persistent_bias_sigma: 0.15  # permanent bias on match ratings (X2: ratings only from sampled events)

tactics:
  formations: ["4-4-2", "4-3-3", "3-5-2"]
  styles: [possession, gegenpress, lowblock]
  rps:                    # rps[a][b] = attacking bonus for a playing against b
    gegenpress: {possession: 0.12, lowblock: 0.0, gegenpress: 0.0}
    possession: {lowblock: 0.10, gegenpress: 0.0, possession: 0.0}
    lowblock:   {gegenpress: 0.12, possession: 0.0, lowblock: 0.0}
```

### 7.4 Scouting & hidden information `scouting`

**This is the core of "never reveal the cards".** Each scout network has a **permanent bias** (`bias_sigma`) — repeated scouting only converges to "the truth + that bias". Band width sets the range the agent sees: own squad 80 fixed pts (shown as a CA range of 8), scouted 160, public 300.

```yaml
scouting:
  n_scouts: 4
  bias_sigma: 40          # scout network's permanent bias sigma (re-scouting can't remove it)
  session_sigma: 30       # per-session temporary noise
  own_bias_sigma: 15      # own-squad players carry smaller bias
  own_session_sigma: 10
  band_width_own: 80      # own-squad ability band width (fixed pts) -> displayed CA range 8
  band_width_scouted: 160 # scouted players
  band_width_public: 300  # players with only public info
  youth_star_sigma: 1.0   # uncertainty of youth potential star rating

attributes:
  scale_max: 2000         # CA internal fixed-point; displayed CA = value/10 (i.e. CA 1..200)
```

### 7.5 Economy `economy` + wage/valuation models

Revenue is tied to division and position (relegation halves income). Wages are the biggest cost. Administration has 30/60-day early warnings.

```yaml
economy:
  inflation_revenue: 0.04    # three inflation rates (pure standing-still declines relatively)
  inflation_wage: 0.045
  inflation_transfer: 0.06
  tv_base_M: {1: 60, 2: 16}          # TV base (first vs second division)
  tv_gradient_top_M: {1: 40, 2: 8}   # position gradient (linear from 1st to 16th)
  ticket_price: 80
  commercial_coef_M: 0.42            # commercial income x rep^2/100 (brand is super-linear)
  operating_cost_pct: 0.16           # operating cost as a share of revenue
  prize_champion_M: {1: 25, 2: 6}    # champion prize money
  wage_ratio_warning: 0.85           # wage/revenue > 0.85 triggers a warning
  wage_ratio_fire_floor: 0.35        # chronic under-investment (wage ratio too low) is also seen as sandbagging
  admin_cash_threshold: -0.10        # cash < annual revenue x -10%
  admin_days: 90                     # 90 consecutive days -> administration
  admin_warn_days: [30, 60]          # administration countdown warnings
  parachute: [0.55, 0.30]            # parachute payments (based on pre-relegation TV, paid for 2 years)
  agent_commission: 0.05             # deal commission

wagemodel:
  w0_cents: 1650000          # wage anchor
  k_per_ca_pt: 0.046
  initial_wage_noise: 0.12   # permanent noise on starting wages (prevents solving true CA from wages)
  leverage_last_year: 1.6    # player asking-price leverage in the last contract year

valuation:
  a_cents: 3570000           # value anchor: CA 120 ~ 8M, CA 180 ~ 120M
  age_curve: {17: 0.6, 21: 0.85, 24: 1.0, 28: 0.85, 30: 0.7, 32: 0.45, 35: 0.2}
  contract_lt12: 0.6         # <12 months of contract left -> value at 60%
```

### 7.6 Market AI counter-play `market_ai`

AI sellers price off **their own noisy belief** (not the truth) and adapt to player behavior: repeatedly milking the same source raises prices.

```yaml
market_ai:
  ask_over_value: 1.12       # asking price = perceived value x 1.12
  accept_ratio: 0.90
  max_rounds: 3              # a single negotiation <=3 rounds
  single_source_markup: 0.12 # repeatedly buying from the same seller -> price up
  flip_sell_penalty: 0.10    # quick flips get discounted
  mispricing_abs_max: 0.15   # per-seed random mispricing (X1: makes "information advantage" valuable)
  free_agent_fee_ratio: 0.20 # free-agent signing fee (prevents the zero-fee free-pickup channel being too strong)
```

### 7.7 Development / aging / injury / fatigue

```yaml
development:
  k_dev_weekly: 3.4          # base growth speed; age-17 target ~ +120 CA/year
  age_mult: {20: 2.0, 23: 1.5, 26: 1.0, 99: 0.5}  # younger grows faster
  low_minutes_factor: 0.25   # few minutes -> slow growth (youth needs playing time)

aging:  # per position: [peak age, yearly decline %, cliff age, cliff extra %]
  groups:
    GK: [31, 2.2, 36, 6.0]
    CB: [28, 4.0, 33, 7.5]
    W:  [26, 7.0, 30, 10.0]    # wingers peak earliest, decline fastest
    ST: [27, 5.2, 31, 8.5]
  retire_age: 34

injury:
  base_match_rate: 0.022     # base per-player per-match injury rate
  age30_mult: 1.4            # 30+ more injury-prone
  fatigue70_mult: 2.75       # fatigue >70 spikes injury rate
  tiers:                     # [share, min days, max days]
    - [0.60, 3, 10]          # 60% minor
    - [0.25, 14, 42]
    - [0.12, 60, 120]
    - [0.03, 180, 270]       # 3% season-ending
  season_ender_perm_phys_loss: [0.03, 0.08]  # severe injury permanently cuts ability

fatigue:  # activated in v0.3: squad depth has mechanical value
  per_match: {GK: 16, CB: 30, W: 34, ST: 32}  # fatigue accrued per match
  recovery_base: 3.9         # daily recovery (a single starter playing every match accrues, ~20-man rotation is neutral)
  thresh1: 70                # fatigue >70 drops performance to 0.80
  thresh1_perf: 0.80
  thresh2: 85                # >85 drops to 0.55
  thresh2_perf: 0.55
```

### 7.8 Stops & budgets `stops`

```yaml
stops:
  fixed:                     # fixed calendar stops (day-of-year -> stop type)
    2: PRESEASON_BOARD
    5: WINDOW_SUMMER_PLAN
    59: DEADLINE_SUMMER
    90/120/150/240: MONTHLY_REPORT
    181: WINDOW_WINTER_OPEN
    300: SEASON_SETTLEMENT
    305: YOUTH_INTAKE
  offer_batch_days: 15       # minimum interval between offer-batch stops
  query_budget: 30           # query budget per stop
  offer_budget: 10           # negotiation budget per stop
  max_standing_orders: 10    # standing-order cap (deliberately crippled)
  notebook_max_chars: 32000  # notebook hard cap
```

---

## 8. Determinism & fairness

**Seeded RNG (reproducible)**: all randomness goes through one counter-based seeded RNG, split by `(domain, entity, day)`. The same seed + the same action string evolves the world bit-identically. `seed + action log` replays a whole run exactly — benchmark results are auditable, and this is the basis of leaderboard anti-cheat (external submissions must attach a replayable signed log).

**Single-entrance obs/actions isolation**: the engine has only two doors to the outside — `engine/obs/` (the only read path, where all output is filtered into ranges/tiers) and `engine/actions/` (the only write path). All three clients (the evaluation runner, the human-played Web UI, external agents' MCP) can only go through these two doors, and importing the internal `domain/` is forbidden. The hidden-info leak surface thus collapses into one file, easy to audit.

**AI truth isolation (engine invariant)**: all decision code of the 15 scripted opponent clubs (buy/sell, pricing, manager changes) is **forbidden to read true ability values** and may only use its own noisy belief — facing the same fog of information as the agent under test. This is enforced on CI by an AST static check (any `engine/` module importing `baselines/` or reading true attributes fails CI). The opponents are "part of the exam room", not cheating examiners.

**Strict human/agent parity**: every button in the Web UI maps to one agent tool, generated from the same schema; every number a human sees comes from the same observation interface as the agent — even "flipping pages consumes query budget" is identical. This guarantees human scores can go on the same leaderboard as model scores.

---

*This document is auto-exported from engine source and real run output. All packet / tool-returns are real dumps from seed=1, 5-year track; the prompt / schema / params are verbatim source. Dumps for other seeds or a specific stop type (e.g. injury stops, offer-negotiation stops) can be regenerated on request.*

---

# 中文版 (Chinese)

以下是上文的中文版。

# FM Bench — 规则与机制导出（供同行审阅）

本文件是面向同行的导出，摊开一个 LLM agent 在 FM Bench 里实际收到什么、能做什么，外加胜负/计分与数值机制，原样列出，方便审阅者对照自己的 agent benchmark。

游戏本身是一个足球俱乐部经理人模拟，已锁定以下产品模式（名称已敲定）：**Solo**、**Arena**、**Open Track**。模拟世界为单一联赛、共 **16 家俱乐部**，最多 **15 个 scripted 对手** 作为校准基线。

文档按以下顺序：
1. **概览** — agent 在玩什么。
2. **System prompt** — agent 收到的完整原文 system prompt（真实的 9701 字符引擎产物）。
3. **Stop packet** — 每回合真实 `stop packet` dump（seed=1，5 年赛道），非手写。
4. **Tool 返回** — agent 看到的 observation 层真实查询输出（seed=1）。
5. **Tools** — agent 能调用的 26 个 tool，含精确 JSON schema。
6. **胜负/计分机制** — 一局怎么结束、分怎么算。
7. **数值机制** — 注解版 `params.yaml`（单一真相源）。
8. **确定性与公平** — 可复现性与人机平权。

所有以 **prompt / packet / tool 返回 / schema / 参数** 形式展示的东西都是**引擎真实产物或源码原文**（用 seed=1、5 年赛道跑出来的），非示意性 mock。这些原文块与语言无关，只在本文档**英文版中出现一次**，就是引擎吐出的原样——刻意**不**在中文版里重复。

---

> 本文件面向正在做类似 **agent-benchmark 游戏** 的同行。目标是把**agent 实际拿到的东西**（system prompt、每回合的 context/stop packet、可调用的 tools）以及**胜负机制与数值机制**原样摊开，方便对照评估。
>
> 文中所有 **prompt / packet / tool 返回 / schema / 参数** 均为**引擎真实产出或源码原文**（用 seed=1、5 年赛道跑出来的），非手写示意。散文双语（完整英文版在上，完整中文版在下）；代码/JSON/参数保持英文原样，只出现一次。

---

## 1. 概览

FM Bench 是一个"足球俱乐部经理人"模拟，用来测 LLM agent 的**长程经营决策智力**。Agent 执掌一家虚构俱乐部，经营固定的 N 个游戏年（通常 5 或 20），除了不控制球员踢球（比赛由引擎根据阵容/战术模拟），其余全管：转会、合同、战术、青训、设施、财务。终局按一个 composite score 打分。

交互是**事件驱动的回合制**。引擎按天推进，遇到需要 agent 表态的事就停下来（转会窗、报价、伤病、月报、董事会警告、赛季结算……），打包一个 **stop packet** 发给 agent。每个 stop 是一次**全新对话**（不累积历史）：runner 发去 `system prompt（固定）+ 本回合 stop packet`，agent 在这个 stop 内进行**多轮 tool-calling 小循环**（查询→行动→再查询……），最后调 `advance` 结束本回合、推进时间。实测一个 5 年赛道约 85–90 个 stop，每 stop 约 5–12 次 API 调用。

**跨 stop 不带对话记忆**。agent 的延续性只靠两样：① 引擎的**只读档案**（`get_history`、比赛报告、财务等，随时可查的客观事实）；② agent 自己的**笔记本**（notebook，每个 stop packet 自动带上，容量硬上限）。"会不会给未来的自己留有用的笔记"本身就是被测能力之一。

---

## 2. System prompt

Runner 组装方式（`runner/render.py`）：`system = ROLE_PREAMBLE + "\n\n---\n\n" + rules.md`。这段前缀 + 规则书**逐字节冻结**（不含时间戳、不随 stop 变化），以便命中 prompt cache。agent 收到的 system prompt **完整原文**（9701 字符）见上方英文版 §2 System prompt。

---

## 3. Stop packet

每个 stop，runner 把 packet 渲染成一条 user message：`"DECISION STOP {stop_id}\n" + json.dumps(packet, indent=1, sort_keys=True)`（`runner/render.py: render_packet`）。packet 的所有字段都经过 observation 层过滤（`engine/sim/stops.py: build_packet`），**不含任何真实隐藏数值**。

packet 字段含义：`digest`（一眼看全的俱乐部状态：排名/现金/工资比/董事会信心/赛季目标/伤病/下一场/开窗与否/`warnings[]`）、`inbox` + `action_required`（上一 stop 以来的事件，部分需回应）、`pending_offers`（进行中的转会谈判）、`recent_actions`（最近动作，供连续性）、`notebook`（agent 自己的笔记）。

下面两个 packet dump 是**引擎真实产出**（seed=1，5 年赛道，非手写）。（完整两个 dump 见上方英文版 §3 Stop packet。）

### 3.1 开局第一个决策停（S0001：季前董事会 + 合同到期提醒）

注意 `inbox` 里的 `board_briefing`：这是开局董事会简报，明确告诉 agent"工资单已接近董事会容忍线，且赛季期望锚定在你接手的阵容上——卖光阵容不会降低考核标准"（防"缩编 sandbag"）。（原文 dump 见上方 §3.1。）

### 3.2 夏季转会窗规划停（S0002：WINDOW_SUMMER_PLAN）

（原文 dump 见上方 §3.2。）

---

## 4. Tool 返回

Agent 在 stop 内调 query tool 看到的也是 observation 层的过滤输出。**关键点：能力永远是区间 + 置信度字母，绝不给点估计。** 真实 dump（seed=1）见上方英文版 §4 Tool returns：

- 自己队球员：`confidence: "A"`，band 宽 8（如 121–129）；
- 本队之外的球员：`confidence: "B"`（band 宽 16）或 `"C"`（band 宽 30，如 85–115）——越不了解越宽。

反复侦察同一个球员**不会收敛到真值**，只会收敛到"真值 + 该球探网络的固有偏差"（见 §7 scouting 参数）。

### 4.1 `get_squad`（前 2 名，共 25 名 — 真实输出）

（原文 dump 见上方 §4.1。）

### 4.2 `get_transfer_market`（截取 2 条 — 真实输出）

注意这两名都是 `status: "expiring"`（合同剩 9 个月），一个 band B 一个 band C——市场上球员的不确定性明显高于自己队。（原文 dump 见上方 §4.2。）

非法调用返回 `{"ok": false, "error": {"code": ..., "hint": ...}}`，**不扣分也不改变世界**，hint 不含精确数值（防止用报错反推隐藏值）。

---

## 5. Tools

Agent 可调用 **26 个 tool**（`engine/actions/commands.py`，其中 24 个常驻，另 2 个为 draft 阶段专用；未开启 draft 时返回 `no_draft`）。每个都以严格 JSON schema 暴露给模型。按类分组的完整参数表（查询类 / 行动类 / 笔记类 / 控制类，`*` 标记必填参数）见上方英文版 §5 Tools。

- 查询类（query — 只读，每停 30 次预算）
- 行动类（action — 谈判类每停 10 次预算）
- 笔记类（note）
- 控制（control）

**预算**：查询类每 stop 30 次，谈判类（`make_transfer_offer` + `respond_to_offer`）每 stop 10 次，`advance` 后重置。`digest` 和 `notebook` 免费（每个 packet 自带）。

一个完整 schema（`make_transfer_offer`，模型实际看到的 function 定义）见上方 §5。

---

## 6. 胜负机制

### 6.1 游戏怎么结束（三条结算路径）

一局在下面任一情况结束，并记 `settle_reason`：

| 结局 | 触发 | 后果 |
|---|---|---|
| `completed` | 跑满配置的 N 年 | 无折扣（rho = 1.0） |
| `fired` | 董事会信心过低（见 §7 board） | 立即结算 + rho 折扣（越早被炒越惨） |
| `administration` | 现金连续 90 天低于 "年收入 × −10%"（`admin_days: 90`, `admin_cash_threshold: -0.10`） | 立即结算 + rho 折扣 + 扣分/扣联赛积分 |

被炒/破产/中途弃局都走**提前结算**：分数的正部乘以 `rho(t)`（完成度的幂函数），负部不打折。

### 6.2 分数公式（score_v0.2，原文来自 `score/composite.py`）

分数公式原文（含 VA 与 rho 定义）见上方英文版 §6.2。

三通道各自 **log 压缩**（边际递减，逼平衡建队）后加权求和：

- **Honors（荣誉）**：整局累计奖杯分（联赛冠军/前四/升级；降级扣分；二级联赛权重远低于一级）。
- **Net worth（净值）——记的是「增量 VA」不是余额**：最后 3 季平减净值均值**减去开局净值**。**零分 = "把俱乐部还回来时和接手时一样值钱"**，赚了才是正分。净值 = 现金 + 折价阵容 + 设施 − 未摊销转会费；买人同时记资产和现金/费用两侧，所以"疯狂买人"刷不了分。
- **Squad value（阵容市值）**：最后 3 季均值（防结算前突击拉盘），权重较低，纯稳定项。

**为什么是「增量」而非「绝对值」**（round-4 校准；逐轮校准日志为内部记录，不在本次发布内）：v0.1 用绝对财富，导致"开局家底运气"解释了 34% 的分数方差、技术只占 16%；减去自身开局家底后翻转为 13% / 33%，并修正了硬 seed 下的排序倒挂。`rho` 只乘正部——所以"早点被炒来止损"不成立（无套利）。分数只在**同赛道长度**内可比（rho 基数随年限变）。

---

## 7. 数值机制

以下为 `params.yaml`（单一真相源）关键块的**原文 + 中文注解**。规则书对 agent **隐藏**这些常数（"empirically learning the world is part of the game"），此处应 owner 要求对同行公开。各块原始 yaml 见上方英文版 §7 对应小节（§7.1–§7.8）；下面保留中文注解。

### 7.1 计分权重 `score`

（yaml 见上方 §7.1。）权重与除数决定三通道的相对影响与边际递减；`honors` 子表给各奖杯的荣誉分（一级冠军 100、前四 20、二级冠军 30、升级 15、一级降级 −40）；`last_n_seasons: 3` 表示净值/市值取最后 3 季均值；`rho_base` / `rho_exp` 为提前结算折扣的基数与指数。

### 7.2 董事会与解雇 `board`

董事会信心是"生存货币"。信心由 结果 vs 期望、排名 vs 赛季目标、财务纪律 驱动。期望**按满阵容实力**定（防摆烂阵容降期望）。（yaml 见上方 §7.2。）要点：爆冷赢 +6、常规赢 +3；工资比破警戒线每月罚 −3，若工资比在下降则罚减半（奖励"正在修复"）；上半区排名给信心托底 25；第 1 年月度罚减半（新手保护）；信心 <20 触发解雇投票（概率 0.6），<10 直接解雇，>80 给转会预算加成；赛季期望地板向真实阵容衰减，允许 1–2 年重建。

### 7.3 比赛模拟 `match` + 战术 `tactics`

比分 = 分段 Poisson（`base_lambda` × 主场优势 × 实力比^alpha × 战术/状态修正）。战术是 rock-paper-scissors，但总影响封顶 ±0.20，**不是胜负手**。（yaml 见上方 §7.3。）`alpha` 越大强队越碾压；换风格需 2 场磨合；`rps[a][b]` 为 a 打 b 的进攻加成。

### 7.4 球探与隐藏信息 `scouting`

**这是"永不揭牌"的核心。** 每个球探网络有一个**永久偏差**（`bias_sigma`）——反复侦察只收敛到"真值 + 该偏差"。band 宽度决定 agent 看到的区间：自己队 80 fixed pts（显示为 CA 区间 8），侦察过 160，公开 300。（yaml 见上方 §7.4。）另注：`session_sigma` 为单次侦察的临时噪声，自己队球员偏差更小；`attributes.scale_max: 2000` 为 CA 内部定点，显示 CA = value/10（即 CA 1..200）。

### 7.5 经济 `economy` + 工资/估值模型

收入按联赛级别和排名挂钩（降级收入腰斩）。工资是最大支出。破产有 30/60 天预警。（yaml 见上方 §7.5。）要点：三种通胀率（纯守成会相对衰落）；转播费按级别与排名梯度（第 1 到第 16 名线性递减）；商业收入 × rep²/100（品牌超线性）；工资/收入 >0.85 报警，工资比过低（<0.35）也会被董事会视为摆烂；降落伞金按降级前级别转播费发 2 年。工资模型对开局工资加永久噪声（防用工资反解真实 CA）；估值模型 CA 120 ~ 8M、CA 180 ~ 120M，合同剩 <12 个月身价打 6 折。

### 7.6 市场 AI 反制 `market_ai`

AI 卖家的定价基于**自己的 noisy 认知**（不是真值），并对玩家行为自适应：反复薅同一来源会涨价。（yaml 见上方 §7.6。）要点：要价 = 认知价值 × 1.12；单次谈判 ≤3 轮；反复从同一卖家买会涨价；快速倒卖会被折价；每 seed 随机错定价（让"信息优势"有价值）；自由球员签字费（防零费白捡通道过强）。

### 7.7 成长/老化/伤病/疲劳

（yaml 见上方 §7.7。）成长基速对 17 岁目标约 +120 CA/年，年龄越小越快，上场少则成长慢（青训要给时间）；老化各位置给 [峰值年龄, 年衰退%, 悬崖年龄, 悬崖额外%]，边锋峰值最早、衰退最快，34 岁退役；伤病每人每场基础率 0.022，30+ 与疲劳 >70 大幅提升，分四档轻重、3% 赛季报销且可能永久掉能力；疲劳（v0.3 激活，阵容深度有了机械价值）每场累积，单主力打满会累积、~20 人轮换则中性，>70 表现降到 0.80、>85 降到 0.55。

### 7.8 回合与预算 `stops`

（yaml 见上方 §7.8。）固定日历停按 day-of-year 映射停类型（季前董事会、夏窗规划、夏窗截止、月报、冬窗开、赛季结算、青训招募）；报价批处理停最小间隔 15 天；每停查询预算 30、谈判预算 10；standing order 上限 10（刻意做残）；笔记本硬上限 32000 字符。

---

## 8. 确定性与公平

**Seeded RNG（可复现）**：所有随机走一个 counter-based 的 seeded RNG，按 `(domain, entity, day)` 分流。同一个 seed + 同一串 action，世界演化逐位一致。`seed + action log` 可精确重放整局——benchmark 结果可审计，也是 leaderboard 防作弊的基础（外部提交需附可重放的签名日志）。

**obs/actions 单入口隔离**：引擎对外只有两扇门——`engine/obs/`（唯一读路径，所有输出都在此过滤成区间/档位）和 `engine/actions/`（唯一写路径）。三个客户端（评测 runner、人玩的 Web UI、外部 agent 的 MCP）都只能走这两扇门，禁止 import 内部 `domain/`。隐藏信息泄漏面因此收敛到一个文件，便于审计。

**AI 真值隔离（引擎不变量）**：15 个 scripted 对手俱乐部的所有决策代码（买卖、定价、换帅）**禁止读真实能力值**，只能用它们自己带噪声的认知——和被测 agent 面对同样的信息迷雾。这条用 AST 静态检查挂在 CI 上强制（`engine/` 任何模块 import `baselines/` 或读 true attributes 都会让 CI 失败）。对手是"考场的一部分"，不是作弊的考官。

**人机严格平权**：Web UI 的每个按钮对应 agent 的一个 tool，由同一份 schema 生成；人看到的每个数字都来自 agent 同款 observation 接口——连"翻页面消耗查询预算"都一致。这保证人类分数可以和模型分数进同一张榜。

---

*本文档由引擎源码与真实运行产出自动导出。所有 packet / tool 返回为 seed=1、5 年赛道的真实 dump；prompt / schema / 参数为源码原文。如需其它 seed 或某个具体 stop 类型（如伤病停、报价谈判停）的 dump，可再生成。*
