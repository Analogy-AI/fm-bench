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

## Pre-season draft (draft worlds only)

Some worlds begin with a **draft stop** (`stop_types: ["DRAFT"]`) before day
1: your club starts with an empty squad and a fixed budget, and every club —
yours and all rivals — builds its initial squad from the SAME shared pool of
50 player templates. Picks are independent: clubs draft copies, so another
club taking a template does not block yours. Unpicked templates disappear
permanently.

Tools: `get_draft_pool` lists every template with position, age, your
scout's ability band, potential stars, price, preset wage and contract
length, plus your budget. `submit_draft` submits your complete pick list
(resubmitting replaces it; `auto_fill: true` repairs an invalid list from
the cheapest tier). Then call `advance` to lock the draft and start the
season.

A valid list has at least the stated minimum number of players including a
goalkeeper, and fits the budget. The best players are priced so a full
star XI is unaffordable — balance stars, depth (fatigue punishes thin
squads over a season) and money: unspent budget plus a stipend becomes your
opening cash, and your board's season target scales with the squad you
build. Wages on preset contracts start paying from day 1.

## Sealed-bid transfers (sealed-market worlds only)

When the sealed market is active, an offer for another club's player does
not resolve immediately: it returns `queued`, and all offers on that player
made the same day resolve together at day end. If you are the only bidder,
normal negotiation follows (accept / counter / reject in your inbox). If
several clubs bid, the highest acceptable bid wins **at its own price**;
losers learn only who won, never the amount. Nobody sees your bid before
resolution, and clearing fees never appear in the public archive, so bid
your own valuation — waiting to "react" to rivals is impossible by design.
Your live bids are capped by your cash (exposure): withdraw a queued offer
if you need the room. Selling works as before: offers on your players
arrive as a batch and you choose.

## Deaths and revival (revival worlds only)

Being fired or entering administration does not end the run immediately:
up to three times, the board grants a second chance — confidence resets
low, a rescue fund arrives, and play continues. Each death multiplies the
positive part of your final score by 0.8 (two deaths: 0.64, and so on),
and the rescue money is excluded from your value-added, so dying is never
profitable — only survivable. The fourth death is final. Every club in the
league, including rivals, receives the same administration bailouts.
