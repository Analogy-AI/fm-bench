# FM Bench Complete Guide

> A football club manager simulation game that is also a benchmark measuring an LLM agent's long-horizon management intelligence.
> Humans and AI play strictly the same game; the score measures the ability to "make correct long-term decisions in an uncertain world."
>
> This document is written for readers encountering the project for the first time: after reading it you should know what it is, how to play it, how an agent runs on it,
> why each design choice was made the way it was, and why you should trust the engineering.

---

## Table of Contents

1. [One-Page Summary: What It Is, Why We Built It](#1-one-page-summary)
2. [The Game World](#2-the-game-world)
3. [How Humans Play](#3-how-humans-play)
4. [How Agents Play](#4-how-agents-play)
5. [Scoring and the Ladder](#5-scoring-and-the-ladder)
6. [Five Difficulty Pillars: Why They Hit LLMs Exactly Where It Hurts](#6-five-difficulty-pillars)
7. [Opponents and Fairness](#7-opponents-and-fairness)
8. [Engineering Trustworthiness: How We Broke and Then Fixed Our Own System](#8-engineering-trustworthiness)
9. [Evaluation Protocol: How Others Use It](#9-evaluation-protocol)
10. [Current State, Open Issues, and Roadmap](#10-current-state-and-roadmap)

---

## 1. One-Page Summary

**What FM Bench is**: You (or an LLM agent) take charge of a fictional mid-table football club and
manage it for 5 to 20 game years — buying and selling players, negotiating contracts, setting lineups, managing finances, developing youth,
and dealing with a board that can fire you at any moment. At the end you get a composite score:
**weighted honors + club net worth + squad market value**, log-compressed, no ceiling.

**Why use a manager game to measure agent intelligence**:

- **The interaction shape fits naturally**. A manager game is turn-based + structured decision-making, which is exactly the shape of LLM
  tool-calling. Unlike an action game, you don't have to wrestle with vision and reflexes; everything measured is decision-making.
- **Long-horizon planning is a gap in existing benchmarks**. The vast majority of agent evaluations end within a few dozen steps;
  here one 5-year run is about 90 decision stops, and 20 years is about 341-416. Youth investment pays off five years later; a
  high-wage signing blows up three years later. **Credit assignment spanning hundreds of steps
  is exactly the shortcoming current LLMs are most criticized for**, and it is the core test item of this benchmark.
- **The score has real meaning**. "Can this model run a club well for ten years" tells you far more about whether an
  agent can be trusted with long-term tasks than an abstract percentage does.

**Architecture in one sentence**: A headless Python engine is the single source of truth, and on top of it sit three thin faces —
the Web UI that humans play, the runner that runs evaluations, and the MCP interface for external agents. Every button a human clicks and
every tool an agent calls correspond one-to-one, generated from the same schema, with strictly equal information.

```
                ┌─────────────────────────┐
                │  Game Engine (Python, deterministic) │  ← world state, rules, archives
                └────────────┬────────────┘
                             │ same set of APIs (obs read / actions write)
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
       Web UI (humans)   Runner (agent)   MCP (external agent)
```

**Current measured landscape** (5-year track, score_v0.2 incremental scoring, zero = "the club is worth
the same when you hand it back as when you took over"): on public seeds 1-5 the calibrated god-mode oracle
(`oracle_v2`) sits at +50.1, the disciplined script at +3.2, and random flailing at −6.3 (§5.2).
⚠ The LLM figures quoted in §8.4 predate the current calibration and the current opponent protocol, and are
**superseded** — see the note there before citing any model number from this guide.

**Rejected alternatives**: directly using real football data (rejected: models would memorize real players,
only a procedurally generated brand-new fictional world per seed can rule out knowledge cheating); building two different
games for humans and agents (rejected: scores would then be incomparable, and human results couldn't enter the same leaderboard).

---

## 2. The Game World

### 2.1 World Structure

- **1 division × 16 teams (16 clubs)**. About 470 players live at any time (16×25 senior squads plus youth academies), ~2,000 distinct over a 20-year run, all procedurally generated
  (name, nationality, position, ability, potential, injury proneness), a brand-new world per seed.
- **Game year = 360 days** (12 months × 30 days), 30 league rounds per division per season.
  Two transfer windows, summer and winter. The engine ticks by "day": matches, injuries, training growth, buying and selling by
  AI clubs, daily financial settlement — advancing in a fixed phase order, with all random numbers going through a seeded RNG.

### 2.2 Hidden Information: The Cards Are Never Revealed

Each player has a true ability value (CA) and potential value (PA) that are **never public, from start to finish**.
Any player — human, agent, or the AI clubs — sees only:

- **Scout reports**: a coarse interval + a confidence tier (e.g. "62-71, confidence B"), never a point estimate;
- **Match performance**: ratings generated only from sampled match events, carrying inherent noise;
- **Key design: scouts carry persistent bias**. Scouting the same player repeatedly,
  your estimate will only converge to "true value + your scout's bias", never reaching the true value.

Why this is core: if the true value could be ground out, judging players would degrade into the grind of "farm more scouting attempts";
noisy observations with persistent bias force all players to make **Bayesian-style inferences** — "the report says potential is high, but the sample
is only 5 matches, is it worth 20 million?" — that's intelligence, not grinding.
(Rejected alternative: reveal the cards once you've scouted enough times. Vetoed by the red team, for the reason above.)

### 2.3 Economy: With Teeth

- Revenue = broadcast + gate + commercial + prize money, tied to league level and standing; relegation halves revenue
  (parachute payments pay the broadcast fee at the **pre-relegation** level for two years, the only cushion).
- Wages are the biggest expense. The healthy band (wages/revenue 55-70%) is a hard constraint that was calibrated:
  the median of AI clubs across the league falls within this band.
- **Bankruptcy is real**: if cash stays below the warning line (about negative 10% of annual revenue) for 90 consecutive days you enter
  administration, with early settlement and a heavy score penalty. A badly run club really does die,
  but the death process has two-tier bankruptcy warnings at 30/60 days, leaving a window to save yourself (sell players, cut wages).

### 2.4 The Board: The Sword Overhead

The board has season expectations (a standing target based on squad strength) and a confidence value: results, financial discipline,
and upsets won or lost all raise or lower it. When confidence runs out you are **sacked** — the run settles early, discounted by
elapsed-time proportion (rho decay). At the very first stop of a run, the board gives a qualitative briefing (e.g. "your wage bill is already near
the board's comfort line"), identical for human and machine.

### 2.5 Player Careers

Young players grow with playing time, veterans over 30 decline, and a serious injury may permanently cut ability. The youth academy produces
new prospects each season; whether to sign them and how much playing time to give is the classic "spend money now, see results in five years" decision.

---

## 3. How Humans Play

Two forms, both playing the same set of rules:

**① Web UI (full version)**: Start the engine service locally, open a browser and play. The interface is a
"manager's desk": a top status bar (date, standing and trend, cash, board confidence gauge,
remaining query/offer budget for this stop), the left-side **inbox** is the main battlefield — transfer offers are
Accept / Reject / counter directly, injuries, renewals, and youth events are handled one by one; right-side tabs:
squad (ability shown as **interval bars** with confidence shading, never a single number), tactics, standings,
transfer market, finances (wage-health-band gauge + cash trend line), historical archive, notebook.
When done, click **Continue ▶** and time fast-forwards to the next thing requiring a stance.
When you finish (or are sacked / go bankrupt), the settlement screen appears: the three channels of the score are revealed item by item + a ladder comparison.

**② Single-file playable demo**: A 38KB HTML file, double-click and play, zero dependencies, zero network,
with a built-in lightweight JS simulator that keeps the core mechanics (hidden intervals, counter-offers, board, settlement score).
Used for zero-barrier sharing; official scores come only from the Python engine.

**Human-machine isomorphism is an iron law**: every button in the UI corresponds to one agent tool, generated from the same schema
file, with CI doing field-level cross-checking; every number a human sees comes from the same observation interface the agent uses —
**even "a human flipping through pages consumes query budget" is consistent with the agent**. This guarantees that human scores
can go on the same leaderboard as model scores.

---

## 4. How Agents Play

### 4.1 Decision Stop: A Turn Is Not a Time Unit, It's an Event

The engine simulates by day, fast-forwarding all the way, and **stops only when something requires a player stance**: a transfer offer, a key-player serious injury,
a contract expiry, a board warning, a monthly-report heartbeat, a season node, a bankruptcy warning. About 20 stops per season
(13 fixed nodes + ~6 event stops), a full 5 years is about 85-95 stops, 20 years about 341-416 stops.

Rejected alternatives: fixed weekly turns (50 weeks a season, most weeks have nothing to do, purely burning tokens
and diluting attention), fixed monthly turns (a transfer window can't fit ten deals in one month, and the off-season idles).

### 4.2 A Brand-New Conversation Per Stop + A Small In-Stop Multi-Turn Loop

At each stop, the runner **assembles a brand-new conversation on the spot** to send to the model under test:

```
[system]  rule book rules.md + 26 tool definitions       ← byte-level frozen, eats prompt cache
[user]    this stop's state packet:
          situation summary (standing/cash/board confidence/burn trend)
          + inbox (items requiring a stance)
          + action summary of the last 5 stops
          + full notebook text (written by the agent itself, ≤8K tokens)
```

Within a stop is a small tool-calling loop — the model returns a tool call each time, the runner executes it and pastes the
result back into the conversation, then calls the next, until the model calls `advance()` (the only way to advance time,
whose return value is the next stop's state packet). Measured at 5-12 API calls per stop, a 5-year run is
about 350-700 calls. A soft convergence reminder at 25 turns, a 40-turn hard cutoff handled by the default policy.

**No conversation carries across stops**. The next stop is a clean start again; continuity relies on two things:

- **Archive queries**: 11 read-only tools (players, standings, history, finances…), facts are always
  queryable and don't need to be remembered;
- **Notebook**: 2 write tools, 8K token hard cap, auto-injected by the runner each stop.
  Judgments and strategy ("I suspect #204's report is inflated", "rebuild the defense in the summer 2031 window") can only rely on it
  to pass to your future self. **What to write and what to delete is itself a tested capability** — the cap is deliberately held at 8K,
  precisely to test the discipline of memory tradeoffs.

Rejected alternative: a rolling long conversation + truncation (a 20-year 400-stop run inevitably blows context, and "what truncation
happens to leave behind" becomes an uncontrollable luck factor; per-stop reassembly + notebook turns memory management into an
explicit, evaluable skill).

### 4.3 The Tool Panel (26 tools)

| Category | Count | Examples |
|---|---|---|
| Query (30/stop budget) | 11 | get_squad, get_player, get_league_table, get_history |
| Action (negotiation type 10/stop budget) | 10 | make_transfer_offer, respond_to_offer, offer_contract, set_lineup, set_tactics, invest |
| Notes | 2 | append_note, rewrite_notes |
| Control | 1 | advance |

The budget is a design choice, not cost-saving: information isn't free, and spending queries on the decisions genuinely close to the line is part of the skill.
There are also standing orders (e.g. "auto-reject offers below X") — **deliberately crippled**, allowing only
single-condition threshold rules, at most 10, to prevent an agent from writing an automaton at the first stop and then going AFK for 20 years.

### 4.4 API Mechanics and Cost

A model under test only needs **an API key + function calling ability**. The runner translates each vendor's API
dialect into a unified interface (Anthropic native, plus an OpenAI-compatible port covering GPT and various local
models; adding a new vendor is about 50 lines). Each call is a stateless HTTPS request; the prefix of the rule book + tool definitions
is byte-level stable, prompt cache hit rate measured at 80-95%.

Engineering safety nets: typed retries (rate-limit backoff, timeouts), a global spend circuit breaker (cumulative billing across the whole call;
when tripped the current run settles as an abandoned run and the remaining seeds are skipped), and **checkpoint resume**
(every action is written to disk in real time, `--resume` replays the action log to rebuild state and continue; the final hash of a resumed run matches an uninterrupted run bit-for-bit).

---

## 5. Scoring and the Ladder

### 5.1 The Composite Score (score_v0.2: incremental scoring)

Three channels are each log-compressed and then weighted, **with no ceiling**:

- **Honors**: championships/top-four/promotion etc. weighted by level, relegation deducts points; computed on a league-tier net basis,
  which blocks the arbitrage of "deliberately relegating to a lower level to farm championships" and the "promotion-relegation elevator";
- **Club net worth — it records the increment (value added), not the balance**: the inflation-deflated net-worth mean of the
  last three seasons, **minus this seed's starting net worth**, scored with sign.
  **A zero score means "the club is worth the same when you hand it back as when you took over"**; profit is positive,
  squandering is negative. (Net worth = cash + assets − liabilities; unamortized transfer fees count as liabilities, which blocks
  the "buy players on expiring contracts to instantly boost the books" arbitrage; excess hoarded cash counts at a discount, so sitting on savings isn't worth it.)
- **Squad market value**: takes the **mean of the last three seasons** rather than an end-of-run snapshot, to prevent a last-minute pump before settlement;
  weight reduced to a stability term.

**Why we changed from absolute to incremental scoring** (calibration round 4): absolute scoring leaked "your starting endowment"
directly into the score — in a rich-seed you could go AFK and still get a high score, and in a hard seed even the oracle finished bottom. After changing to
incremental, variance decomposition measured: **world luck share 34%→13%, skill share 16%→33%**,
and hard seeds no longer scramble the whole ladder. One vetoed variant is on record: making all three channels incremental
would let "frantically buying players before bankruptcy" rush to the top (squad appreciation has no cash-side reconciliation); making just the net-worth channel
incremental books both sides of every deal exactly.

Sacking, bankruptcy, and abandoned runs all take the same early-settlement path: the **positive part** of the score is multiplied by a rho(t) discount
(a power function of elapsed-time proportion; only the positive part is discounted, otherwise "getting sacked early to cut losses" would become arbitrage).
A human abandoning mid-run and an agent being sacked follow the same rule; two sets of standards are forbidden.

**Why log + no ceiling**: a benchmark most fears saturation (the MMLU-style 90-point club).
Honors and net worth can accumulate without limit, and log compression keeps score density reasonable while the ceiling always stays above your head.

### 5.2 The Ladder: The Meaning of a Score Is Defined by Anchors

Scripted anchors, re-measured on the engine as shipped. Regenerate this table
at any time with `python scripts/anchor_table.py --seeds 1 2 3 4 5 --years 5`;
it stamps its own provenance, so a stale copy is detectable.

Measured on `engine_commit b5b18de` / `params_hash 06ea9d609104` / `score_v0.2`,
5-year track, public seeds 1-5 (mean of 5 seeds, one repeat each):

| Anchor | Definition | Mean | Range (min..max) | Outcome |
|---|---|---:|---|---|
| `random` | uniform sampling of legal actions | **−6.30** | −14.36 .. −1.01 | 3/5 fired, 2/5 completed |
| `idle` | pure AFK (never acts, only advances) | **+0.54** | −4.95 .. +3.41 | all fired |
| `greedy` | buys expensive, no long-term investment | **−3.84** | −7.38 .. −0.29 | 3/5 completed, 2/5 fired |
| `heuristic` | disciplined deterministic FM script | **+3.20** | +0.17 .. +7.97 | 4/5 fired, 1/5 completed |
| `oracle_v0` | god-mode script, conservative historical ceiling | **+3.33** | +0.06 .. +7.01 | 3/5 completed, 2/5 fired |
| `oracle` (`oracle_v2`) | god-mode script, calibrated ceiling | **+50.12** | +43.28 .. +58.02 | all completed |
| human expert | 3-5 FM veterans proctored, same protocol | — | — | to be organized (v1) |

Note which agent each oracle row is: `--agent oracle` runs **`oracle_v2`**
(`baselines/oracle_v2.py`); `oracle_v0` (`baselines/oracle.py`) is the older,
conservative ceiling and is selectable separately. They are an order of
magnitude apart, so quoting "the oracle" without saying which one is
meaningless.

**Two properties of this ladder currently fail on this seed set, and are stated
rather than hidden:**

1. **`idle` (+0.54) outscores `greedy` (−3.84).** A null policy that only calls
   `advance` beats an active-but-reckless one, so "order preservation" does not
   hold across the whole ladder. `scripts/anchor_table.py` reports inversions
   explicitly.
2. **`oracle_v0` (+3.33) is within noise of `heuristic` (+3.20).** The
   conservative ceiling is not a ceiling on this seed set — consistent with it
   being described elsewhere as soft per-seed.

The Oracle is an information-value **ceiling**, not a live cheat tripwire. A
high score is not itself a red flag: strong legitimate play now approaches
`oracle_v2` on the 20-year track (internal measurement — the ladder in §5.2
above is 5-year, so no 20-year oracle figure is published yet), and §8.4 documents a legitimate seat crossing
`oracle_v0`, so any absolute "approaching the Oracle" threshold would fire on
clean runs. Leak detection is instead the **truth-isolation test suite** — the
observation layer provably never emits a true value (AST-checked, and swept
tool-by-tool against `data/reference_world_seed1.json`) — plus trace audit of
how a score was earned. Read the Oracle as headroom, and the isolation tests,
not a score threshold, as the alarm.

**Methodology: difficulty is not designed, it is calibrated.** After each change we rerun all anchors,
and it only counts if all four criteria (order preservation / discrimination / floor / non-saturation) pass. Scores are only comparable within the same track
(same year length) and the same calibration version, and this is written into the protocol — every result file is stamped with
`engine commit + params hash + score version`, and the statistics tools loudly warn against mixing versions in one table.

**Track length is itself a measurement parameter**: measurement shows a 3-year track collapses discrimination — skill doesn't have time to
compound, and random, AFK, and the disciplined script all cram onto one line, cheap but basically only able to test "will you die or not";
**5 years is the minimum meaningful track**, and 20 years fully exposes the long-horizon planning gap. This is why the default evaluation is set at 5 years.

---

## 6. Five Difficulty Pillars

Each one corresponds to a known shortcoming of current LLMs — this is the structural guarantee that "even top models can't get high scores",
rather than relying on making the rules complicated:

1. **Never-revealed hidden information** → hits **valuation calibration under noise**. The difference between judging players well and judging them blind
   is an order of magnitude in transfer efficiency over 20 years. Measured: sonnet gets reeled in by the player agent's "wants
   even more" and keeps raising the price (12→14→17→18M/year), with no resistance to the anchoring effect.
2. **Delayed rewards** → hits **long-horizon credit assignment**. Youth pays off five years later, a stadium loan amortizes over ten
   years; doing the right thing gives no reward signal at the moment.
3. **Compounding penalties (a survivable death spiral)** → hits **trend recognition and loss-cutting**. Signing a dud at high wages
   → wages blow out → no money to reinforce → relegation → revenue halved, the snowball rolls for three seasons;
   the engine gives a qualitative burn trend and two-tier bankruptcy warnings, and whether you can brake is a real score difference.
4. **A market that fights back** → hits **strategy monotony**. Poaching from the same source repeatedly gets bid up,
   frequent flipping has friction, and any "one trick" gets automatically priced up by the environment to be not worth it — structural
   exploit protection, more reliable than manual hole-plugging.
5. **Multi-objective tension + board sacking** → hits **multi-constraint balancing**. You must both survive the present
   (confidence, results) and lay out for ten years (youth, finances); both hunkering down to save money and going all-in on stars will kill you,
   just in different ways. Both death modes have been hit by models in measurement: sonnet went bankrupt in all three runs, haiku
   was sacked in all.

---

## 7. Opponents and Fairness

### 7.1 What Those 15 Opponents Are

**Pure scripts** (hand-written rules: buy for whatever position is missing, bid by their own noisy valuation, keep wage
discipline, switch tactics by opponent strength), zero LLM calls. They're not smart, but they're strictly disciplined.

**Why they must be scripts and not other models**: the benchmark's difficulty comes from the environment, not from
the opponents' brains; it is a "management test on harsh terrain", not chess. If the opponents were adaptive
LLMs, the world faced when testing model A and testing model B would differ, and the scores would immediately lose comparability —
the track doesn't need to run faster than you, the track needs to be identical for everyone.
(Model vs. model competition is another medal, see §9 Arena.)

**"Won't weak scripts cap the ceiling too low"**: currently the "not smart but disciplined" environment pins both tiers of Claude
models below baseline, the bar is far from topped out; when models eventually clear it, opponent strength is parameterized
(can be fed stronger incrementally), the score has no ceiling, and the Arena provides an adversarial dimension.

### 7.2 AI True-Value Isolation: CI-Enforced, Not a Verbal Promise

Engine iron law: **any AI club's decision code is forbidden from reading a player's true attributes**, it can only use its
own noisy cognition — enforced by an AST static scan hooked into CI. This rule has actually caught a violation
(see §8.2's "quote inversion"), it's not for show.

### 7.3 Determinism and Auditing

- All random numbers go through a counter-based RNG split by (domain, entity, day), with a fixed day-level
  phase order and a ban on depending on dict iteration order;
- **seed + action log = bit-level replay of the whole run**. Same seed, same actions, and two runs produce a final
  state hash that matches bit-for-bit; this is the foundation of anti-cheat auditing — a leaderboard run must submit a log
  verifiable by replay (HMAC-signed);
- Official evaluation uses a **secret seed set**, invalidated by rotation if leaked; public seeds can be self-tested by anyone.
- A 20-year full-speed simulation takes 76 seconds (single core), which makes calibration regressions and counterfactual experiments cheap.

---

## 8. Engineering Trustworthiness

The value ceiling of a benchmark is determined by its biggest hole. This system's trustworthiness comes not from
"we wrote it very carefully", but from **process: first let independent eyes break it, then fix it, then verify it**.
Everything below has commits, tests, and data to check.

### 8.1 How It Was Designed

The design phase was done by 12 independent roles dividing the work: 8 domain designs (gameplay, engine, economy, evaluation
science, agent interface, red team, football domain, product) each produced a draft, 3 cross-reviews (overall architecture
arbitration, red-team review, scope trimming) picked at each other's conflicts, 1 aggregated. The red team was at the table from day one —
"how to farm the score" and "how to make it fun" were discussed with equal weight.

### 8.2 Dual Review + Dual Verification: 11 Confirmed Holes

After the engine was built, two independent reviewers (one checking determinism and information leakage, one red-teaming
the economy and scoring) + two independent verifiers (reproducing each on the spot) cross-confirmed 11 issues,
**each with a runnable attack reproduction**. The five nastiest:

| Hole | Attack path | Fix |
|---|---|---|
| **Solving true value from wages** | Initial wages are a noise-free function of true CA, and inverting the formula yields the whole squad's true values, 3× better than scouting (mean error 5.4 vs 15.3 fixed-point) | Initial wages now generated from the club's own noisy cognition + coarse-tier quantization; added an "observation regression audit" test |
| **Money printer economy** | Revenue/wages differed by 2× in magnitude, and over 5 years zero clubs across the league went cash-negative or bankrupt — financial intelligence couldn't be measured at all, every cash exploit was free | Full-parameter recalibration: wages/revenue median falls at 0.55-0.70, bankruptcy is reachable; hooked to a calibration regression test |
| **Offer-probe oracle** | Offers didn't count against budget, 60 shots in one stop, and a counter-offer = an invertible midpoint, piecing together the whole league's true-value map (error 1.4 CA points, 2.3× more accurate than scouting) | Offers now count against budget (10/stop), ~15% price-tier quantization, counter-offers randomized proportionally, withdrawals add friction; regression test asserts the price channel is never more accurate than scouting |
| **Lineup-setting is a no-op** | set_lineup was overridden by "auto-pick strongest XI" (deliberately field the 11 weakest, only 1 player plays) — rotation/blooding kids all fail | Player choice takes priority in placement; added a "weak squad is respected" test. **Collateral finding**: this bug masked another "field a weak lineup to farm board upset points" hole, and both must be fixed together, otherwise fixing the former alone actually arms the latter |
| **Replay fork** | When advancing via the tool path, the action log advanced twice and subsequent actions were silently dropped — the audit guarantee was broken on the real agent path | Replay protocol fix + full-tool-path replay test; abandoned runs are also logged and replayable |

These holes were all fixed before any official score was recorded, confirmed by a second round of verification.

### 8.3 Three Rounds of Calibration: Each Round Evidence-Driven

- **Round 1**: The root cause of greedy's inverted ordering wasn't a parameter, it was that its offers were always rejected (bringing 5M
  to haggle over a 60M star) — fix the strategy, not the constant; also fixed the settlement-order artifact of "finishing a full run yet being
  'sacked' on the last day for a 24% discount". A belief-cache optimization compressed the 20-year simulation from 230s
  to 76s, proven to have zero side effects by "final hash matches bit-for-bit".
- **Round 2 (LLM autopsy-driven)**: A month-by-month ledger reconstruction of sonnet's first run showed a death spiral
  locked in over 10 months (design target 2-3 seasons), and the score was actually measuring "how many months you survive the board".
  The attribution verdict: **65% genuine decision errors + 25% calibration problems + 10% interface friction**. After fixing
  and restoring the channel, a counterfactual replay from its **original action log**: the same actions, under old parameters
  got sacked at confidence 9, under new parameters survived at confidence 29.5 — the failure was rerouted to a survivable financial bankruptcy.
  Baseline mean moved <3%, proving the loosening hit precisely on the LLM's cause of death without watering things down.
- **Round 3 (synchronized bankruptcy investigation)**: sonnet went bankrupt on the same day at t≈2.25 years across three runs, suspected
  a mechanical trap. A cash-ledger replay verdict: **it deserved to die** — wage bill ≥ revenue, cash 2-11× deep into the warning line,
  and the synchrony was merely caused by the season-bonus calendar; the economy wasn't changed. But two fairness
  issues were found and fixed: the 90-day bankruptcy fuse was invisible to the player (added 30/60-day warnings, replay proving
  they'd fire before each of its deaths), and 92% of tool errors (1,043 of them) stemmed from an
  interface ergonomics flaw (contract renewal missed passing the years parameter, changed to default carry-over).

### 8.4 LLM Measurement: Low Scores, and Demonstrably Low for Good Reasons

> ⚠ **SUPERSEDED — do not cite these model numbers.** This section records an
> early measurement round. It predates the current calibration
> (`params_hash 06ea9d609104`), the protocol-v0.3 opponent set, and the
> reasoning-on harness; the scripted anchors quoted in it are also stale
> (current values in §5.2). Later campaigns on the current engine measure
> frontier models **far above** the scripted anchors rather than below them, so
> the section heading no longer describes the evidence. It is kept for the
> autopsy methodology — how a failure is attributed to capability rather than
> to rules friction — and the reruns are pending. The seed count is also
> internally inconsistent below ("same 3 seeds" against 3/5 and "all three
> runs"), another reason to treat only the method as current.

Same track (5 years) × same 3 seeds, score_v0.2 incremental scoring (zero = handing it back at preserved value):

| Player | Mean score (v0.2) | Outcome |
|---|---|---|
| heuristic script | +2.34 | 3/5 finished |
| **claude-sonnet** | +0.96* | **all three runs went bankrupt (t≈2.25)** |
| **claude-haiku** | +0.53* | all sacked (2.1-3.8 years) |
| greedy | −0.25 | mostly sacked |
| random | −5.30 | destroyed value |

(* The two models' numbers were measured before the round-3 fixes, affected by the now-fixed interface friction and warning
absence, and should be treated as lower bounds; rerun scheduled.)

Three verifiable conclusions:

1. **Top models genuinely can't get high scores** — and the autopsy proves the main cause is a real capability shortcoming
   (ignoring the rule book's financial-discipline warnings, still raising renewal offers while confidence fell from 50 to 9,
   being led by negotiation anchoring), not obscure rules;
2. **The scoring philosophy changed the model ordering, and changed it correctly**. Under absolute scoring (v0.1)
   "sonnet loses to haiku", under incremental scoring it reverses to sonnet > haiku — sonnet is
   the only one attacking (buying stars, grabbing honors, then going bankrupt), and old scoring double-penalized its team-building investment
   with "deduct cash + don't credit the squad"; after incremental scoring books both sides of a deal, its decision
   quality advantage (notably stronger notes and self-awareness in the autopsy) shows up in the score.
   The model-ordering conclusion still needs more seeds and reruns to support;
3. **Failure modes map one-to-one to the design pillars** (§6), showing the difficulty comes from what we want to test.
   This is the route of "first make the score low, then guarantee it's low for good reason".

**Multi-model preliminary test (3-year track, numbers pending verification)**: the first cross-vendor sweep (two tiers of Claude +
two tiers of Gemini, 3 years × 3 seeds) has finished, but a shape of "a legal agent scoring above the
oracle" appeared — **this is exactly the cheat alarm from §5.2 firing** — and before the leak-investigation
conclusion is out, this batch of numbers is treated entirely as provisional, with no model-comparison claims made.
(The discrimination limits of the 3-year track itself, see §5.2.)

### 8.5 Quality Baseline

About 6,300 lines of Python core (plus ~1,900 lines of Web front-end), 104 tests all green, covering:
determinism (same seed double-run hash equality), full-tool-path replay consistency, AI true-value isolation
AST scan, no point estimates in observations, economy calibration bands, crash-resume equivalence, golden-hash behavior
freeze (new features like the Arena may not change a single byte of Benchmark Mode behavior).

---

## 9. Evaluation Protocol

FM Bench has three locked modes:

- **Solo** (official 1v15, fixed harness, feeds the Solo leaderboard): we run it — locked model id/temperature/prompt, **secret seed set**, mean over multiple seeds. A custom harness is forbidden.
- **Arena** (official, feeds the Arena leaderboard): 16 clubs in one world, each seat a competing agent (a seat may be a scripted anchor for calibration), competing head to head. A custom harness is forbidden.
- **Open Track** (self-serve 1v15, your own harness — the only mode allowing a custom harness): you run it in your own environment (may plug in a full agent framework); submit an HMAC-signed action log, server-side bit-level replay verification, no leaderboard if it doesn't match. Tests an agent system rather than a bare model.

| Mode | Who runs | Anti-cheat | Purpose |
|---|---|---|---|
| **Solo** | we run: lock model id/temperature/prompt, **secret seed set**, mean over multiple seeds; official 1v15, fixed harness | seed secret + full logging | official cross-model comparison → Solo leaderboard |
| **Arena** | official 16-LLM competition, fixed harness | seed secret + full logging | model-vs-model competition → Arena leaderboard |
| **Open Track** | you in your own environment (may plug in a full agent framework); self-serve 1v15, your own harness | submit HMAC-signed action log, server-side bit-level replay verification, no leaderboard if it doesn't match | testing an agent system rather than a bare model |

**Note on official modes**: both official modes (Solo, Arena) forbid a custom harness — only Open Track allows one.
The MCP interface (your agent connects directly to the game and manages its own context) is available under Open Track, for holistic evaluation of memory systems / multi-model collaboration.

Script baselines are free to run; LLM-run cost scales with model tier, run length
and seed/repeat count, with the 80-95% prompt cache hit already factored in.

---

## 10. Current State and Roadmap

### Done

- Engine v0 (world of 1 division × 16 teams / matches / economy / board / youth / market fightback, deterministic + replay; engine default being updated to 1×16)
- Runner productionized (Anthropic / OpenAI-compatible / Gemini three-provider adapters,
  cache 80-95%, checkpoint resume, global circuit breaker)
- Five baselines (including the Oracle information ceiling) + four rounds of evidence-driven calibration
  (round 4 = score_v0.2 incremental scoring + result-file version stamping)
- Web UI (playable) + single-file demo (shareable); Arena (16 clubs)
- 104 tests, golden-hash behavior freeze

### In Progress / To Do

- **Multi-model 3-year sweep leak investigation**: the alarm shape "a legal agent above the oracle" appeared,
  the batch's numbers are frozen as provisional until the investigation concludes;
- **Model rerun**: retest haiku/sonnet under the same conditions after round-3/4 (current numbers are a lower bound)
- **Human baseline**: 3-5 FM experts proctored, filling in the high anchor
- **Deepen information premium on the engine side** (long-tail scout bias, deeper seed-level mispricing —
  round 4 filed but not implemented, to avoid isolating a run in progress)
- **20-year long-track calibration** (short-track constants don't extrapolate), official leaderboard service (seed rotation,
  submission verification), Web UI v1 (rendering new warnings/trends), Arena seat opening-briefing completion

### Honest Open Issues

- **The tier spacing is improving but not up to standard**: v0.2 pulled the random↔greedy gap from 1.45 to
  5.05 points, CIs no longer overlap, but there's still distance to "a ladder everyone reads at a glance", and the engine-side
  lever (deepening information premium) is filed;
- **Interaction variance still 54%**: world luck has been compressed from 34% to 13%, but the strategy×world
  interaction term (partly the honest "playstyle fit") is still large; measure first, then decide whether to keep tightening;
- **Cross-model discrimination not yet proven**: both tiers of Claude scores still hug zero under incremental scoring,
  the cross-vendor numbers are in leak investigation, and "the tiers spread out" can only be claimed after reruns + the investigation conclusion.

These issues are written here rather than hidden, because a benchmark's credibility =
**others can verify every sentence you say**: every score has a replayable log, every calibration has
counterfactual evidence, every known defect is numbered on record.

---

*Engine, runner, and rule book are in this repo: `rules.md` (the player rule book, human-machine
same source), `params.yaml` (every tunable constant), `docs/RULES_EXPORT.md` (system prompt, stop
packet and all 26 tool schemas), `docs/SCORING.md` (the score formula), and `tests/` (the
determinism, replay, truth-isolation and calibration invariants as executable checks).*

*The internal design records this guide cites in passing — the design master document, the locked
decision log, and the round-by-round calibration log — are **not** part of this open release. Where
they carried a load-bearing invariant, that invariant is encoded as a test instead, which is the
form a reader can actually verify.*

---

# 中文版 (Chinese)

以下是上文的中文版。

# FM Bench 完全指南

> 一个足球俱乐部经理人模拟游戏,同时是一个测量 LLM agent 长程经营智力的 benchmark。
> 人和 AI 玩的是严格同一个游戏;分数衡量的是"在不确定的世界里做长期正确决策"的能力。
>
> 本文写给第一次接触本项目的读者:读完你应该知道它是什么、怎么玩、agent 怎么跑、
> 每个设计为什么这样定、以及凭什么相信这套工程。

---

## 目录

1. [一页摘要:这是什么,为什么做](#1-一页摘要)
2. [游戏世界](#2-游戏世界)
3. [人怎么玩](#3-人怎么玩)
4. [Agent 怎么玩](#4-agent-怎么玩)
5. [计分与阶梯](#5-计分与阶梯)
6. [五大难度支柱:为什么恰好打在 LLM 的短板上](#6-五大难度支柱)
7. [对手与公平](#7-对手与公平)
8. [工程可信度:我们怎么攻破又修好自己的系统](#8-工程可信度)
9. [评测协议:别人怎么用](#9-评测协议)
10. [现状、遗留问题与 roadmap](#10-现状与-roadmap)

---

## 1. 一页摘要

**FM Bench 是什么**:你(或一个 LLM agent)执掌一家虚构的中游足球俱乐部,
经营 5 到 20 个游戏年——买人卖人、谈合同、排阵容、管财务、养青训、
应付一个随时可能炒掉你的董事会。终局得到一个综合分:
**加权荣誉 + 俱乐部净值 + 阵容市值**,log 压缩、不设上限。

**为什么用经理人游戏测 agent 智力**:

- **交互形态天然契合**。经理人游戏是回合制 + 结构化决策,恰好就是 LLM
  tool-calling 的形状。不需要像动作游戏那样折腾视觉和反应,测的全是决策。
- **长线规划是现有 benchmark 的空白**。绝大多数 agent 评测在几十步内结束;
  这里一局 5 年约 90 个决策停、20 年约 341-416 个,青训投入五年后才回报、
  一次高薪签约三年后才爆雷。**跨越数百步的信用分配(credit assignment)
  正是当前 LLM 最被诟病的短板**,也是这个 benchmark 的核心测项。
- **分数有真实含义**。"这个模型能不能把一家俱乐部经营好十年"比一个抽象的
  百分比更能说明 agent 能否被委以长期任务。

**架构一句话**:一个 headless 的 Python 引擎是唯一真相源,上面接三张薄脸——
人玩的 Web UI、跑评测的 runner、外部 agent 的 MCP 接口。人点的每个按钮和
agent 调的每个 tool 一一对应,由同一份 schema 生成,信息严格平权。

```
                ┌─────────────────────────┐
                │   游戏引擎 (Python, 确定性) │  ← 世界状态、规则、档案
                └────────────┬────────────┘
                             │ 同一组 API (obs 读 / actions 写)
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
       Web UI (人玩)    Runner (agent 玩)   MCP (外部 agent)
```

**当前实测格局**(5 年赛道,score_v0.2 增量计分,零分 = "把俱乐部还回来时
和接手时一样值钱"):在公开 seed 1-5 上,校准后的开天眼 oracle(`oracle_v2`)
约 +50.1,守纪律的脚本约 +3.2,乱操作约 −6.3(见 §5.2)。
⚠ §8.4 中引用的 LLM 数字早于当前校准与当前对手协议,已**被取代**;引用本指南中
任何模型分数前,请先阅读该处说明。

**被拒绝的替代方案**:直接采用真实足球数据(被拒:模型会背真实球员,
每个 seed 程序化生成全新虚构世界才能杜绝知识作弊);给人和 agent 做两套
游戏(被拒:分数将不可比,人类成绩就进不了同一个 leaderboard)。

---

## 2. 游戏世界

### 2.1 世界结构

- **1 级联赛 × 16 队(16 个俱乐部)**。任意时刻约 470 名球员(16×25 一线阵容 + 青训),20 年累计约 2,000 名,全部程序化生成
  (姓名、国籍、位置、能力、潜力、伤病体质),每个 seed 一个全新世界。
- **游戏年 = 360 天**(12 个月 × 30 天),每级联赛 30 轮周赛。
  夏冬两个转会窗。引擎按"天"tick:比赛、伤病、训练成长、AI 俱乐部的
  买卖、财务日结,固定相序推进,全部随机数走 seeded RNG。

### 2.2 隐藏信息:永不揭牌

每名球员有一个**从头到尾不公开**的真实能力值(CA)和潜力值(PA)。
任何玩家——人、agent、AI 俱乐部——看到的都只有:

- **球探报告**:一个粗区间 + 置信度档位(例如 "62-71, 置信 B"),永远不给点估计;
- **比赛表现**:评分只从采样出的比赛事件生成,自带噪声;
- **关键设计:球探带永久偏差(persistent bias)**。反复侦察同一球员,
  你的估计只会收敛到"真值 + 你的球探的偏差",永远到不了真值。

为什么这条是核心:如果真值可以磨出来,识人就退化成"刷侦察次数"的体力活;
带永久偏差的噪声观测逼所有玩家做**贝叶斯式推断**——"报告说潜力高,但样本
只有 5 场,值不值 2000 万?"——这是智力,不是体力。
(被拒绝的替代方案:侦察次数够多就揭牌。被红队否决,理由如上。)

### 2.3 经济:有牙齿

- 收入 = 转播 + 票房 + 商业 + 奖金,与联赛级别和排名挂钩;降级收入腰斩
  (降落伞金按**降级前**级别的转播费发两年,是唯一的缓冲)。
- 工资是最大支出。健康区间(工资/收入 55-70%)是校准出来的硬约束:
  全联盟 AI 俱乐部的中位数落在这个带内。
- **破产是真的**:现金连续 90 天低于警戒线(约负的年收入 10%)即进入
  administration,提前结算、分数重罚。经营不善的俱乐部真的会死,
  但死亡过程有 30/60 天两级破产警告,给自救留了窗口(卖人、砍薪)。

### 2.4 董事会:悬在头上的剑

董事会有赛季期望(按阵容实力定的排名目标)和一个信心值:战绩、财务纪律、
爆冷输赢都会增减它。信心耗尽即**下课**——该局提前结算,按已进行时间打
折扣(rho 衰减)。开局第一停,董事会会给一份定性简报(如"你的工资单已在
董事会的舒适线附近"),人机同文。

### 2.5 球员生涯

年轻球员靠上场时间成长,30+ 老将衰退,重伤可能永久掉能力。青训营每季
产出新人,签不签、给多少上场时间,是典型的"现在花钱、五年后见效"决策。

---

## 3. 人怎么玩

两种形态,玩的都是同一套规则:

**① Web UI(完整版)**:本地起引擎服务,浏览器打开即玩。界面是一个
"经理人办公桌":顶部状态条(日期、排名与走势、现金、董事会信心仪表、
本停剩余查询/报价额度),左侧**收件箱**是主战场——转会报价直接
Accept / Reject / 还价,伤病、续约、青训事件逐条处理;右侧标签页:
阵容(能力显示为带置信度阴影的**区间条**,永远不是一个数)、战术、积分榜、
转会市场、财务(工资健康带仪表 + 现金走势线)、历史档案、笔记本。
处理完点 **Continue ▶**,时间快进到下一个需要表态的事。
打完(或被炒/破产)出结算画面:三通道分数逐条揭晓 + 阶梯对照。

**② 单文件试玩 demo**:一个 38KB 的 HTML 文件,双击就玩、零依赖零联网,
内置一个轻量版 JS 模拟器,保留核心机制(隐藏区间、还价、董事会、结算分)。
用于零门槛分享;正式分数只出自 Python 引擎。

**人机同构是铁律**:UI 的每个按钮对应 agent 的一个 tool,由同一份 schema
文件生成,CI 做字段级对照;人看到的每个数字都来自 agent 同款 observation
接口——**连"人翻页面消耗查询额度"都和 agent 一致**。这保证了人类分数
可以和模型分数放进同一张榜。

---

## 4. Agent 怎么玩

### 4.1 决策停(decision stop):回合不是时间单位,是事件

引擎按天模拟,一路快进,**遇到需要玩家表态的事才停**:转会报价、主力重伤、
合同到期、董事会警告、月报心跳、赛季节点、破产警告。每季约 20 停
(13 个固定节点 + ~6 个事件停),5 年打满约 85-95 停,20 年约 341-416 停。

被拒绝的替代方案:固定周回合(一季 50 周,大部分周没事可做,纯烧 token
且稀释注意力)、固定月回合(转会窗一个月十笔生意塞不下,淡季又空转)。

### 4.2 每停一个全新对话 + 停内多轮小循环

每到一停,runner **现场组装一个全新对话**发给被测模型:

```
[system]  规则书 rules.md + 26 个 tool 定义        ← 字节级冻结,吃 prompt cache
[user]    本停状态包:
          战况摘要(排名/现金/董事会信心/烧钱趋势)
          + 收件箱(待表态事项)
          + 近 5 停行动摘要
          + 笔记本全文(agent 自己写的,≤8K tokens)
```

停内是一个 tool-calling 小循环——模型每次返回 tool 调用,runner 执行后把
结果贴回对话,再调下一次,直到模型调 `advance()`(唯一推进时间的方式,
其返回值就是下一停的状态包)。实测每停 5-12 次 API call,一局 5 年
约 350-700 次调用。软性收敛提醒在 25 轮,40 轮硬截止按默认策略处理。

**跨停不带对话**。下一停又是干净开局,延续性靠两样东西:

- **档案查询**:11 个只读 tool(球员、积分榜、历史、财务……),事实永远
  可查,不需要记;
- **笔记本**:2 个写入 tool,8K token 硬上限,runner 每停自动注入。
  判断和战略("我怀疑 204 号的报告虚高""2031 年夏窗重建后防")只能靠它
  传给未来的自己。**写什么、删什么,本身就是被测能力**——上限刻意压在 8K,
  就是要考记忆的取舍纪律。

被拒绝的替代方案:滚动长对话 + 截断(20 年 400 停必然爆 context,且"截断
碰巧留下什么"会变成不可控的运气因素;每停重组 + 笔记本把记忆管理变成
显式的、可评的技能)。

### 4.3 Tool 面板(26 个)

| 类别 | 数量 | 例子 |
|---|---|---|
| 查询(每停 30 次预算) | 11 | get_squad, get_player, get_league_table, get_history |
| 行动(谈判类每停 10 次预算) | 10 | make_transfer_offer, respond_to_offer, offer_contract, set_lineup, set_tactics, invest |
| 笔记 | 2 | append_note, rewrite_notes |
| 控制 | 1 | advance |

预算是设计,不是省钱:信息不免费,把查询花在真正接近的决策上是能力的一部分。
另有 standing orders(如"低于 X 的报价自动拒")——**刻意做残**,只允许
单条件阈值规则、最多 10 条,防止 agent 第一停写好一个自动机然后挂机 20 年。

### 4.4 API 机制与成本

被测模型只需要**一把 API key + 会 function calling**。runner 把各家 API
的方言翻译成统一接口(Anthropic 原生、OpenAI 兼容口覆盖 GPT 和各类本地
模型,新增一家约 50 行)。每次调用是无状态 HTTPS 请求;规则书 + tool 定义
的前缀字节级稳定,prompt cache 命中率实测 80-95%。

工程兜底:类型化重试(限流退避、超时)、全局花费熔断(整次调用累计计费,
触线时当前局按弃局结算、剩余 seed 跳过)、**断点续跑**(每个动作实时落盘,
`--resume` 重放动作日志重建状态后继续,实测续跑与不间断跑最终 hash 逐位一致)。

---

## 5. 计分与阶梯

### 5.1 综合分(score_v0.2:增量计分)

三通道分别 log 压缩后加权,**不设上限**:

- **荣誉**:冠军/前四/升级等按级别加权,降级扣分;按联赛层级净值计算,
  堵死"故意降级去低级别刷冠军"和"升降级电梯"套利;
- **俱乐部净值——记的是增量(value added),不是余额**:最后三个赛季的
  通胀平减净值均值,**减去这个 seed 的开局净值**,带符号计分。
  **零分的含义是"你把俱乐部还回来时,和接手时一样值钱"**;赚了才是正分,
  败家就是负分。(净值 = 现金 + 资产 − 负债;未摊销转会费计为负债,堵
  "买临期合同球员瞬间拉账面"的套利;超额囤现金打折计入,躺平攒钱不划算。)
- **阵容市值**:取**最后三个赛季均值**而非期末快照,防结算前突击拉盘;
  权重降为稳定项。

**为什么从绝对分改成增量分**(校准 round 4):绝对分把"开局家底"直接
泄进分数——富矿 seed 里挂机都能拿高分,硬 seed 里 oracle 也垫底。改成
增量后,方差分解实测:**世界运气占比 34%→13%,技术占比 16%→33%**,
硬 seed 不再把整个阶梯打乱。一个被否决的变体记录在案:三通道全部做增量
会让"破产前疯狂买人"冲到榜首(阵容增值没有现金侧的对账),净值单通道
增量正好把每笔交易的两侧都记账。

被炒、破产、弃局都走同一条提前结算路径:分数的**正部**乘以 rho(t) 折扣
(进行时间占比的幂函数;只折正部,否则"早点被炒来止损"会变成套利)。
人类中途弃局与 agent 被炒同规则,禁止两套标准。

**为什么 log + 无上限**:benchmark 最怕饱和(MMLU 式的 90 分俱乐部)。
荣誉和净值可以无限累积,log 压缩保证分数密度合理,天花板永远在头顶之上。

### 5.2 阶梯:分数的意义由锚点定义

脚本锚点,基于当前发布代码重新实测。任何时候都可用
`python scripts/anchor_table.py --seeds 1 2 3 4 5 --years 5` 重新生成;
输出自带 provenance 戳,因此过期的副本可被识别。

实测环境:`engine_commit b5b18de` / `params_hash 06ea9d609104` / `score_v0.2`,
5 年赛道,公开 seed 1-5(5 个 seed 的均值,各 1 次重复):

| 锚点 | 定义 | 均值 | 区间(min..max) | 结局 |
|---|---|---:|---|---|
| `random` | 合法动作均匀采样 | **−6.30** | −14.36 .. −1.01 | 3/5 被炒,2/5 打完 |
| `idle` | 纯挂机(只调用 advance) | **+0.54** | −4.95 .. +3.41 | 全部被炒 |
| `greedy` | 买贵不投长线 | **−3.84** | −7.38 .. −0.29 | 3/5 打完,2/5 被炒 |
| `heuristic` | 守纪律的确定性 FM 脚本 | **+3.20** | +0.17 .. +7.97 | 4/5 被炒,1/5 打完 |
| `oracle_v0` | 开天眼脚本,保守历史天花板 | **+3.33** | +0.06 .. +7.01 | 3/5 打完,2/5 被炒 |
| `oracle`(`oracle_v2`) | 开天眼脚本,校准后的天花板 | **+50.12** | +43.28 .. +58.02 | 全部打完 |
| 人类高手 | 3-5 名 FM 老手监考,同协议 | — | — | 待组织(v1) |

注意两个 oracle 是不同的 agent:`--agent oracle` 跑的是 **`oracle_v2`**
(`baselines/oracle_v2.py`);`oracle_v0`(`baselines/oracle.py`)是更早的保守
天花板,需单独指定。两者相差一个数量级,因此笼统说"oracle 多少分"没有意义。

**该阶梯目前在此 seed 集上有两条性质不成立,此处如实列出:**

1. **`idle`(+0.54)高于 `greedy`(−3.84)。** 只会调用 `advance` 的空策略
   打败了积极但莽撞的策略,因此"保序"在整条阶梯上并不成立。
   `scripts/anchor_table.py` 会显式报告这类倒挂。
2. **`oracle_v0`(+3.33)与 `heuristic`(+3.20)差距在噪声内。** 在此 seed 集上
   保守天花板并不构成天花板——与其他处"逐 seed 偏软"的描述一致。

Oracle 还被设计为**作弊警报器**("任何 agent 逼近 Oracle 分,大概率是找到了
信息泄漏")。但 `oracle_v2` 在 +50、盲打约在 +3,该判据已无法触发:阈值需要
相对于**最强合法分数**来定义,而不是相对于天花板。在重新定义之前,请将该警报
视为**未指定**;不要把"警报未响"当作没有泄漏的证据。

**方法论:难度不是设计出来的,是校准出来的。**每次改动后重跑全部锚点,
四判据(保序 / 区分度 / 地板 / 不饱和)全过才算数。分数只在同一赛道
(同年限)、同一校准版本内可比,这写进了协议——每份结果文件都盖有
`engine commit + params hash + score 版本`的章,统计工具对混版本合表
高声报警。

**赛道长度本身是个测量参数**:实测 3 年赛道会把区分度压塌——技能来不及
复利,random、挂机和守纪律脚本挤在一条线上,便宜但基本只能测"会不会死";
**5 年是最小有意义赛道**,20 年才充分暴露长线规划差距。这就是默认评测
定在 5 年的原因。

---

## 6. 五大难度支柱

每一条都对应当前 LLM 的一个已知短板——这是"顶级模型也拿不到高分"的
结构性保证,而不是靠把规则搞复杂:

1. **永不揭牌的隐藏信息** → 打在**噪声下的估值校准**上。识人准的和识人瞎
   的,20 年转会效率差一个数量级。实测:sonnet 会被球员经纪人的"还想要
   更多"钓着一路加价(12→14→17→18M/年),对锚定效应毫无抵抗。
2. **延迟回报** → 打在**长程信用分配**上。青训五年后出货、球场贷款十年
   摊销,做对了当下看不到任何奖励信号。
3. **复利式惩罚(可自救的死亡螺旋)** → 打在**趋势识别与止损**上。高薪
   签废人 → 工资爆表 → 没钱补强 → 降级 → 收入腰斩,雪球滚三个赛季;
   引擎给出定性的烧钱趋势和两级破产警告,能不能刹车是真实的分数差。
4. **会反制的市场** → 打在**策略单一性**上。连续从同一来源薅人会被抬价,
   频繁倒卖有摩擦,任何"一招鲜"会被环境自动涨价到不划算——结构性防
   exploit,比人工堵漏可靠。
5. **多目标撕扯 + 董事会下课** → 打在**多约束平衡**上。既要活在当下
   (信心、战绩)又要布局十年(青训、财务),苟着攒钱和梭哈买星都会死,
   只是死法不同。实测两种死法都有模型踩过:sonnet 三局全破产,haiku
   全被炒。

---

## 7. 对手与公平

### 7.1 那 15 个对手是什么

**纯脚本**(手写规则:缺哪个位置买哪个、按自己的噪声估值出价、守工资
纪律、按对手强弱换战术),零 LLM 调用。它们不聪明,但纪律严明。

**为什么必须是脚本而不是别的模型**:benchmark 的难度来自环境,不来自
对手的脑子;它是"恶劣地形上的经营测试",不是下棋。对手若是会自适应的
LLM,测 A 模型和测 B 模型时面对的世界就不同了,分数直接失去可比性——
跑道不需要比你跑得快,跑道需要对每个人一模一样。
(模型 vs 模型的对抗是另一块奖牌,见 §9 Arena。)

**"脚本弱会不会封顶太低"**:目前"不聪明但守纪律"的环境把两档 Claude
模型都摁在 baseline 之下,门槛远未触顶;将来模型通关了,对手强度是参数
化的(可逐步喂强),分数无上限,且 Arena 提供对抗升维。

### 7.2 AI 真值隔离:CI 强制,不是口头承诺

引擎铁律:**任何 AI 俱乐部的决策代码禁止读取球员真实属性**,只能用它们
自己的噪声认知——用 AST 静态扫描挂在 CI 里强制。这条规则真的抓到过violation
(见 §8.2 的"要价反演"),不是摆设。

### 7.3 确定性与审计

- 全部随机数走按 (domain, entity, day) 分流的 counter-based RNG,天级
  相序固定,禁止依赖 dict 遍历序;
- **seed + 动作日志 = 整局 bit 级重放**。同 seed 同操作,两次跑出的最终
  状态 hash 逐位一致;这是防作弊审计的地基——上榜的 run 必须交出可重放
  验证的日志(HMAC 签名);
- 官方评测用**保密 seed 集**,泄漏即轮换作废;公开 seed 任人自测。
- 20 年全速模拟 76 秒(单核),校准回归和反事实实验因此便宜。

---

## 8. 工程可信度

一个 benchmark 的价值上限由它最大的漏洞决定。这套系统的可信度不是来自
"我们写得很小心",而是来自**流程:先让独立的眼睛攻破它,再修,再验证**。
以下全部有 commit、测试和数据可查。

### 8.1 怎么设计的

设计阶段由 12 个独立角色分工完成:8 个领域设计(玩法、引擎、经济、评测
科学、agent 接口、红队、足球领域、产品)各自出稿,3 个交叉评审(总架构
裁决、红队复核、范围裁剪)互挑冲突,1 个汇总。红队从第一天就在桌上——
"怎么刷分"和"怎么好玩"同权重讨论。

### 8.2 双评审 + 双验证:11 个被确认的洞

引擎建成后,两名独立 reviewer(一个查确定性与信息泄漏,一个当红队攻
经济与计分)+ 两名独立 verifier(逐条现场复现)交叉确认了 11 处问题,
**每一处都有可运行的攻击复现**。最狠的五个:

| 洞 | 攻击路线 | 封堵 |
|---|---|---|
| **工资反解真值** | 初始工资是真实 CA 的无噪声函数,反解公式即得全队真值,精度比球探好 3 倍(平均误差 5.4 vs 15.3 定点) | 初始工资改由俱乐部自己的噪声认知生成 + 粗档量化;新增"观测回归审计"测试 |
| **经济印钞机** | 收入/工资量级差 2 倍,5 年全联盟零现金为负、零破产——财务智力无从测起,一切现金 exploit 免费 | 全参数重校准:工资/收入中位落 0.55-0.70,破产可达;挂校准回归测试 |
| **报价探针 oracle** | 报价不计预算,一停 60 发,还价 = 可反演的中点,拼出全联盟真值图(误差 1.4 CA 点,比球探准 2.3 倍) | 报价计预算(10/停)、~15% 价格阶梯量化、还价随机比例化、撤单加摩擦;回归测试断言价格通道永远不比球探准 |
| **排阵形同虚设** | set_lineup 被"自动选最强 XI"覆盖(故意排 11 个最弱,只 1 人上场)——轮换/练小孩全失效 | 玩家选择优先落位;新增"弱阵容被尊重"测试。**连带发现**:此 bug 掩盖着另一个"排弱阵刷董事会爆冷分"的漏洞,两者必须同修,否则单修前者反而武装后者 |
| **重放分叉** | 走 tool 路径推进时动作日志重复推进、后续动作静默丢失——审计保证在真实 agent 路径上是坏的 | 重放协议修正 + 全 tool 路径回放测试;弃局同样落日志可重放 |

这些洞在任何正式分数记录之前全部修复,由第二轮验证确认。

### 8.3 三轮校准:每一轮都由证据驱动

- **Round 1**:greedy 排序倒挂的根因不是参数,是它出价永远被拒(拿 5M
  砍 60M 的球星)——修策略而非改常数;顺手修掉"打完整局却在最后一天被
  '炒'吃 24% 折扣"的结算顺序 artifact。信念缓存优化把 20 年模拟从 230s
  压到 76s,以"最终 hash 逐位一致"证明零副作用。
- **Round 2(LLM 尸检驱动)**:sonnet 首局逐月账本重建显示,死亡螺旋
  10 个月锁死(设计目标 2-3 赛季),分数实际在测"你熬过董事会几个月"。
  归因判决:**65% 真实决策失误 + 25% 校准问题 + 10% 界面摩擦**。修复
  恢复通道后,用它的**原始动作日志反事实重放**:同样的操作,旧参数下
  信心 9 被炒,新参数下信心 29.5 存活——失败改道为可自救的财务破产。
  baseline 均值移动 <3%,证明松绑精准打在 LLM 死因上而没有放水。
- **Round 3(同步破产调查)**:sonnet 三局在 t≈2.25 年同日破产,疑似
  机械陷阱。现金账本重放判决:**该死**——工资单 ≥ 收入、现金深到警戒线
  的 2-11 倍,同步只是赛季奖金日历造成的;经济不改。但查出两个公平性
  问题并修复:90 天破产保险丝对玩家不可见(补 30/60 天警告,重放证明
  会在它每次死亡前拉响),以及 92% 的 tool 错误(1,043 次)源于一个
  接口人机工学缺陷(续约漏传年限参数,改为默认沿用)。

### 8.4 LLM 实测:分数低,而且低得有据可查

> ⚠ **已被取代——请勿引用本节的模型分数。** 本节记录的是一轮早期实测,早于当前
> 校准(`params_hash 06ea9d609104`)、protocol v0.3 对手集与 reasoning-on
> harness;其中引用的脚本锚点同样过期(当前值见 §5.2)。在当前引擎上的后续
> campaign 测得前沿模型**远高于**脚本锚点,而非低于,因此本节标题已不再描述现有
> 证据。保留本节是为了其复盘方法论——如何把失败归因于能力而非规则摩擦——重跑
> 待排期。下方 seed 数也自相矛盾("同 3 seeds" 与 3/5、"三次 run" 并存),这也是
> 只应把方法论视为现行内容的原因。

同赛道(5 年)× 同 3 seeds,score_v0.2 增量计分(零 = 保值交还):

| 玩家 | 均分(v0.2) | 结局 |
|---|---|---|
| heuristic 脚本 | +2.34 | 3/5 打完 |
| **claude-sonnet** | +0.96* | **三局全破产(t≈2.25)** |
| **claude-haiku** | +0.53* | 全被炒(2.1-3.8 年) |
| greedy | −0.25 | 多数被炒 |
| random | −5.30 | 摧毁价值 |

(* 两个模型的数字测于 round-3 修复之前,受已修复的接口摩擦与警告缺失
影响,应视为下界;重跑排期中。)

三个可核查的结论:

1. **顶级模型确实拿不到高分**——而且尸检证明主因是真实的能力短板
   (无视规则书的财务纪律警告、看着信心从 50 跌到 9 仍在续约加价、
   被谈判锚定钓着走),不是规则晦涩;
2. **计分哲学改变了模型排序,而且改对了**。绝对计分(v0.1)下
   "sonnet 输给 haiku",增量计分下反转为 sonnet > haiku——sonnet 是
   唯一在进攻的(买星、抢荣誉、然后破产),旧计分对它的建队投入
   "扣现金 + 不认阵容"双重惩罚,增量计分把交易两侧都记账后,其决策
   质量优势(尸检中笔记与自我认知明显更强)才在分数上显形。
   模型排序结论仍需更多 seed 与重跑支撑;
3. **失败模式与设计支柱一一对应**(§6),说明难度来自我们想测的东西。
   这就是"先让分数低,再保证低得有道理"的路线。

**多模型初测(3 年赛道,数字待核验)**:首轮跨厂商 sweep(两档 Claude +
两档 Gemini,3 年 × 3 seeds)已跑完,但其中出现了"合法 agent 分数超过
oracle"的形态——**这正是 §5.2 所说的作弊警报器在触发**,在泄漏排查
结论出来之前,这批数字一律视为临时值,不作任何模型对比声明。
(3 年赛道本身的区分度局限见 §5.2。)

### 8.5 质量基线

约 6,300 行 Python 内核(另 ~1,900 行 Web 前端),104 项测试全绿,涵盖:
确定性(同 seed 双跑 hash 相等)、全 tool 路径重放一致、AI 真值隔离
AST 扫描、观测无点估计、经济校准区间、崩溃续跑等价、golden-hash 行为
冻结(Arena 等新功能不许改变 Benchmark Mode 的任何字节)。

---

## 9. 评测协议

FM Bench 有三个锁定的 mode:

- **Solo**(官方 1v15,固定 harness,喂 Solo leaderboard):我们跑——锁 model id/温度/prompt,**保密 seed 集**,多 seed 取均值。禁止自带 harness。
- **Arena**(官方,喂 Arena leaderboard):一个 world 内 16 家俱乐部,每个 seat 一个参赛 agent(其中可有一个 scripted anchor 用于校准),正面对抗。禁止自带 harness。
- **Open Track**(自助 1v15,自带 harness——唯一允许自定义 harness 的 mode):你在自己环境跑(可接完整 agent 框架);提交 HMAC 签名动作日志,服务端 bit 级重放验证,对不上不上榜。测 agent 系统而非裸模型。

| 模式 | 谁跑 | 防作弊 | 用途 |
|---|---|---|---|
| **Solo** | 我们跑:锁 model id/温度/prompt,**保密 seed 集**,多 seed 取均值;官方 1v15,固定 harness | seed 保密 + 全程日志 | 官方模型间对比 → Solo leaderboard |
| **Arena** | 官方 16-LLM 对抗,固定 harness | seed 保密 + 全程日志 | 模型 vs 模型对抗 → Arena leaderboard |
| **Open Track** | 你在自己环境跑(可接完整 agent 框架);自助 1v15,自带 harness | 提交 HMAC 签名动作日志,服务端 bit 级重放验证,对不上不上榜 | 测 agent 系统而非裸模型 |

**官方 mode 说明**:两个官方 mode(Solo、Arena)都禁止自带 harness——只有 Open Track 允许。
MCP 接入(你的 agent 直连游戏,自管上下文)在 Open Track 下可用,用于记忆系统/多模型协作的整体评测。

脚本 baseline 免费运行;LLM 运行成本随模型档位、局长与 seed/重复数变化,
prompt cache 命中 80-95% 已计入。

---

## 10. 现状与 roadmap

### 已完成

- 引擎 v0(1 级联赛 × 16 队世界、比赛/经济/董事会/青训/市场反制、确定性 + 重放;引擎默认正在更新到 1×16)
- Runner 生产化(Anthropic/OpenAI 兼容/Gemini 三家 provider 适配、
  cache 80-95%、断点续跑、全局熔断)
- 五个 baseline(含 Oracle 信息天花板)+ 四轮证据驱动校准
  (round 4 = score_v0.2 增量计分 + 结果文件版本盖章)
- Web UI(可玩)+ 单文件 demo(可分享);Arena(16 个俱乐部)
- 104 项测试,golden-hash 行为冻结

### 进行中 / 待做

- **多模型 3 年 sweep 泄漏排查**:出现"合法 agent 超过 oracle"的警报形态,
  排查结论前该批数字冻结为临时值;
- **模型重跑**:round-3/4 之后的 haiku/sonnet 同条件重测(当前数字是下界)
- **人类基线**:3-5 名 FM 高手监考,补上高位锚点
- **信息溢价引擎侧加深**(球探偏差长尾、更深的 seed 级 mispricing——
  round 4 已立项未实施,避免孤立在跑的结果)
- **20 年长赛道校准**(短赛道常数不外推)、正式榜服务(seed 轮换、
  提交验证)、Web UI v1(渲染新警告/趋势)、Arena 席位开局简报补齐

### 诚实的遗留问题

- **档间距在改善但未达标**:v0.2 把 random↔greedy 差距从 1.45 拉到
  5.05 分、CI 不再重叠,但离"人人一眼看懂的阶梯"还有距离,引擎侧
  杠杆(信息溢价加深)已立项;
- **交互方差仍占 54%**:世界运气已从 34% 压到 13%,但策略×世界的
  交互项(部分是诚实的"打法适配度")还很大,先测量再决定是否继续拧;
- **模型间区分度未证明**:两档 Claude 分数在增量计分下仍贴近零,
  跨厂商数字在泄漏排查中,"档次拉得开"要等重跑 + 排查结论后才有
  资格宣称。

这些问题写在这里而不是藏起来,因为 benchmark 的公信力=
**别人能验证你说的每一句话**:每个分数有日志可重放,每次校准有
反事实证据,每个已知缺陷有编号在案。

---

*引擎、runner、规则书均在本仓库内:`rules.md`(玩家规则书,人机同源)、
`params.yaml`(全部可调常量)、`docs/RULES_EXPORT.md`(system prompt、stop packet
与全部 26 个 tool schema)、`docs/SCORING.md`(评分公式)、`tests/`(determinism、
replay、truth-isolation 与校准不变量的可执行检查)。*

*本指南顺带引用的内部设计记录——设计母文档、锁定决议日志、逐轮校准日志——
**不**属于本次开源发布。其中承载不变量的部分已改为以测试形式固化,这也是读者
真正能自行验证的形式。*
