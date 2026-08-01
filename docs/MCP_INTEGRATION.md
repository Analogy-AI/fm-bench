# Provider Integration & MCP

How an external LLM provider points their **own** model, prompts, or agent
harness at FM Bench and gets a comparable score. This answers the reviewer
question: *"If we let each provider write their own harness/prompts, what
information do we expose to them?"*

The short answer: a provider controls **how their agent reasons**; FM Bench
fixes **the world, the observation channel, the tool surface, and the
scoring**. That boundary is what makes two providers' scores comparable.

---

## 1. Two integration levels

| Level | What the provider supplies | What FM Bench supplies | Status |
|---|---|---|---|
| **A. Model only** | a model id + API key | the entire harness (runner, prompt assembly, memory, tool loop) | **shipped** |
| **B. Harness/prompts** | their own agent loop, prompts, memory strategy | the engine, the observation contract, the tool schemas, the scorer | **interface shipped (`league/` controllers); MCP adapter planned** |

Most providers want **Level A** — it is one flag and produces a
leaderboard-standard score with zero integration work:

```bash
export OPENAI_API_KEY=sk-...
python run_benchmark.py --agent claude --model gpt-5 --seeds 1 --years 5
```

The provider matrix (Anthropic / OpenAI / Gemini adapters, key discovery,
prompt caching, crash-safe resume) lives in the runner itself:
`runner/providers.py` for the adapters, `runner/keys.py` for key discovery,
and `docs/REASONING_TIERS.md` for how reasoning effort is equalized. The
README quickstart is the shortest path to a first run.

**Level B** is for providers who want their *own* reasoning scaffold (custom
system prompt, chain-of-thought policy, memory/RAG strategy, multi-agent
committee, etc.) rather than FM Bench's default runner. That is what the rest
of this document covers.

---

## 2. What the agent is exposed to (the observation contract)

An agent — FM Bench's runner or a provider's own harness — interacts with the
game through exactly one read path and one write path. It never touches engine
internals, and it never sees hidden information.

**Per decision stop, the agent receives a stop packet** containing only:

- a **state digest** (date, league position, cash, board confidence, budgets),
- an **inbox** (the events requiring a decision this stop),
- a short **recent-actions** summary,
- the agent's own **notebook** (its persisted free-text memory).

**Player and squad information is always a band + confidence letter, never a
true number.** Repeated scouting converges to *truth + a fixed per-scout bias*,
never to truth.

> **See `docs/RULES_EXPORT.md` for the real artifacts**, captured from a live
> engine run (seed 1, 5-year track):
> - §2 — the verbatim system prompt (`rules.md`),
> - §3 — a real stop packet dump (opening stop + a transfer-window stop),
> - §4 — real tool-return payloads (`get_squad`, `get_transfer_market`),
> - §5 — the full schema of all 26 tools.

A provider building a Level-B harness should treat `RULES_EXPORT.md` as the
interface spec: those packets and schemas are exactly what their model will
see and call.

---

## 3. The tool surface

26 tools in five groups (full schemas in `RULES_EXPORT.md` §5):

| Group | Count | Budget | Examples |
|---|---|---|---|
| query (read-only) | 11 | 30 / stop | `get_squad`, `get_player`, `get_league_table`, `get_history` |
| action (mutating) | 10 | 10 / stop for negotiation | `make_transfer_offer`, `respond_to_offer`, `offer_contract`, `set_lineup`, `set_tactics`, `invest` |
| note (memory) | 2 | — | `append_note`, `rewrite_notes` |
| draft (opt-in phase) | 2 | — | `get_draft_pool`, `submit_draft` — return `no_draft` unless `params.draft.enabled` |
| control | 1 | — | `advance` (ends the stop, returns the next packet) |

The two draft tools are always present in the tool list even when the draft is
off, so the list stays byte-identical and prompt-cacheable across world
variants; off-phase calls return a `no_draft` error and cost nothing.

The budgets are part of the test: information is not free, and spending
queries on the decisions that actually matter is a measured skill. A provider's
harness sees the same budgets and the same digit-free error hints as the
default runner.

---

## 4. How Level-B works: the controller interface

Today the concrete extension point is the **`league/` controller interface**,
the same abstraction that lets a human, a script, a mock, or an LLM occupy any
of the 16 club seats (World = 1 division × 16 teams, i.e. 16 clubs). A seat is
described by a `SeatSpec` and driven by a controller —
`HumanTerminalController`, `LLMController`, `BaselineController`,
`ScriptController`, `MockLLMController`. The multi-seat Arena layer that
defines them is operated by us and is **not** part of this open release; the
single-seat path it wraps is `engine/game.py` + `engine/obs/` +
`engine/actions/`, which is fully present here.

A provider integrating their own harness implements the same controller
contract: given a stop packet, decide, call tools via the engine's
`obs`/`actions` layer, and eventually `advance`. The engine calls the
controller; the controller owns the reasoning. Because every controller goes
through the identical `obs`/`actions` boundary, a custom harness cannot see or
do anything the default runner can't.

> **Engine status (honest):** the controller interface, `obs`/`actions`
> boundary, tools, and scorer are shipped. The world-size change to the locked
> World = 1 division × 16 teams (16 clubs) is **in progress** in the engine;
> until it lands, some code paths still assume the older world shape.

**MCP adapter (planned).** The intended turnkey path for external agents is a
thin MCP server that exposes the same tool surface over the Model Context
Protocol, so any MCP-capable agent can occupy a seat without writing Python.
The design is a 1:1 wrapper over the existing `obs`/`actions` tools (no new
capability, no new information) — it is an adapter, not a second game. Until it
ships, Level-B integration is via the `league/` controller interface above.

---

## 5. How scoring is returned

Every run produces a result JSON (`results/<label>.json`) with the composite
score and its **three transparent channels** — honours, club net-worth
(value-added), squad value — each log-compressed, plus the early-settlement
`rho` factor. The formula and constants are public in `score/composite.py`
and annotated in `RULES_EXPORT.md` §6 (win/lose) and §7.1 (weights). Nothing
about scoring is hidden: a provider can recompute their own score from the
stored channels.

For League/online runs, each seat is scored independently on the same basis.

---

## 6. The three modes (comparability rules)

A provider may supply their own harness **and still land a comparable score**,
but only in one mode — and only as long as the fixed surface stays fixed. The
rule of thumb: *you control the agent's reasoning; you do not touch the game.*

FM Bench has three modes:

- **Solo** — official 1v15 (one tested LLM vs 15 scripted opponents),
  run through **our fixed harness**. Feeds the **Solo leaderboard**. A
  custom harness is **forbidden** here.
- **Arena** — official 16-LLM (16 LLM-driven seats in one world).
  Feeds the **Arena leaderboard**. A custom harness is **forbidden** here.
- **Open Track** — self-serve 1v15 where the provider runs the benchmark in
  their own environment with **their OWN harness**. This is the **only** mode
  that allows a custom harness. Self-reported, replay-verified.

Both official modes (Solo, Arena) **forbid** a custom
harness — the harness itself is part of what makes their scores comparable.
Only **Open Track** allows a provider to bring their own harness.

| Must be **fixed** (or the score is not comparable) | May be **provider-controlled** (Open Track only) |
|---|---|
| engine version (commit + `params_hash`, stamped in every result) | system prompt / instructions to the model |
| official seed set (secret for the leaderboards) | reasoning policy, thinking budget, self-critique |
| tool set + observation contract | memory strategy (how the notebook/history is used) |
| scorer (`score_v0.2`) | multi-agent / committee scaffolding |
| decoding temperature for the official modes | provider-side caching, retries |

- **Solo / Arena (official)** — FM Bench runs the model
  through the official runner with the official prompt, fixed temperature,
  secret seed set, pinned engine version, and **our fixed harness**. Directly
  comparable across models. These feed the Solo leaderboard and the Arena
  Leaderboard respectively.
- **Open Track** — the provider runs their own harness in their own
  environment and self-reports, submitting a signed action log
  (`actions.jsonl`) so FM Bench can **replay it against the pinned engine** and
  verify the score deterministically (`seed + action log → bit-identical
  rerun`). Marked "self-reported, replay-verified." This is the only mode where
  a custom prompt/harness lives.

The engine's determinism (see `RULES_EXPORT.md` §8) is what makes Open Track
verification possible: a submitted action log either replays to the claimed
final state hash or it is rejected.

---

## 7. What a provider cannot do (and why)

- **Cannot see hidden information.** True ability never crosses the `obs`
  boundary; a custom harness gets bands + confidence like everyone else.
- **Cannot change the opponents.** In the official 1v15 modes the 15 opponent
  clubs are scripted (`market_ai`) so that model A and model B face an identical
  world; LLM-driven opponents are reserved for the Arena (16-LLM)
  mode, because scripted opponents are what makes two models' scores
  comparable in the first place.
- **Cannot alter the scorer or the engine.** Both are pinned and stamped into
  the result; an Open Track submission is only accepted if it replays.

These are not restrictions on cleverness — they are the invariants that make a
number mean "this model manages a club well," comparably, across providers.

---

## See also

- `docs/RULES_EXPORT.md` — the real prompt, packets, tool schemas, scoring,
  and numeric params (the interface spec).
- `README.md` — quickstart, evaluation protocol, the mode matrix.
- `docs/REASONING_TIERS.md` — how reasoning effort is equalized across
  providers.

---

# 中文版 (Chinese)

以下是上文的中文版。

# Provider 接入与 MCP

一家外部 LLM provider 如何把他们**自己的** model、prompt 或 agent harness
指向 FM Bench 并拿到一个可比较的 score。本文回答 reviewer 的问题：*"如果我们
让每家 provider 写自己的 harness/prompt，我们会向他们暴露哪些信息？"*

简短的答案：provider 控制**他们的 agent 如何推理**；FM Bench 固定
**world、observation channel、tool surface 以及 scoring**。正是这条边界让
两家 provider 的 score 可比较。

---

## 1. 两个接入层级

| 层级 | provider 提供什么 | FM Bench 提供什么 | 状态 |
|---|---|---|---|
| **A. 仅 model** | 一个 model id + API key | 整个 harness（runner、prompt assembly、memory、tool loop） | **已发布** |
| **B. Harness/prompt** | 他们自己的 agent loop、prompt、memory 策略 | engine、observation contract、tool schema、scorer | **interface 已发布（`league/` controllers）；MCP adapter 计划中** |

大多数 provider 想要 **Level A**——它只是一个 flag，且零集成工作即可产出一个
leaderboard 标准的 score：

```bash
export OPENAI_API_KEY=sk-...
python run_benchmark.py --agent claude --model gpt-5 --seeds 1 --years 5
```

provider matrix（Anthropic / OpenAI / Gemini adapter、key discovery、prompt
caching、crash-safe resume）见 `runner/providers.py`（adapter）、
`runner/keys.py`（key 发现）与 `docs/REASONING_TIERS.md`（推理档位对齐）；
最短上手路径见 README quickstart。

**Level B** 面向那些想要*自己的*推理脚手架（自定义 system prompt、
chain-of-thought 策略、memory/RAG 策略、multi-agent committee 等）而非 FM Bench
默认 runner 的 provider。这正是本文其余部分所讲的内容。

---

## 2. Agent 被暴露到什么（observation contract）

一个 agent——无论是 FM Bench 的 runner 还是 provider 自己的 harness——通过恰好
一条 read path 和一条 write path 与 game 交互。它从不触碰 engine 内部，也从不
看到隐藏信息。

**每个 decision stop，agent 会收到一个 stop packet**，其中只包含：

- 一份 **state digest**（date、league position、cash、board confidence、budgets），
- 一个 **inbox**（本次 stop 需要决策的 events），
- 一份简短的 **recent-actions** 摘要，
- agent 自己的 **notebook**（它持久化的 free-text memory）。

**Player 和 squad 信息永远是一个 band + confidence letter，绝不是真实数字。**
反复 scouting 会收敛到 *truth + 一个固定的 per-scout bias*，永远不会收敛到 truth。

> **真实 artifact 见 `docs/RULES_EXPORT.md`**，捕获自一次实际 engine run
> （seed 1，5-year track）：
> - §2 —— 逐字的 system prompt（`rules.md`），
> - §3 —— 一次真实的 stop packet dump（opening stop + 一个 transfer-window stop），
> - §4 —— 真实的 tool-return payload（`get_squad`、`get_transfer_market`），
> - §5 —— 全部 26 个 tool 的完整 schema。

构建 Level-B harness 的 provider 应把 `RULES_EXPORT.md` 当作 interface spec：
那些 packet 和 schema 正是他们的 model 将会看到和调用的东西。

---

## 3. Tool surface

26 个 tool，分五组（完整 schema 见 `RULES_EXPORT.md` §5）：

| 组 | 数量 | Budget | 例子 |
|---|---|---|---|
| query（read-only） | 11 | 30 / stop | `get_squad`、`get_player`、`get_league_table`、`get_history` |
| action（mutating） | 10 | negotiation 10 / stop | `make_transfer_offer`、`respond_to_offer`、`offer_contract`、`set_lineup`、`set_tactics`、`invest` |
| note（memory） | 2 | — | `append_note`、`rewrite_notes` |
| draft（可选阶段） | 2 | — | `get_draft_pool`、`submit_draft` —— 未开启 `params.draft.enabled` 时返回 `no_draft` |
| control | 1 | — | `advance`（结束 stop，返回下一个 packet） |

即使未开启 draft，这 2 个 tool 也始终出现在 tool list 中，以保证 list 字节级
一致、可命中 prompt cache；未在 draft 阶段调用时返回 `no_draft` error，不计成本。

这些 budget 本身就是测试的一部分：信息不是免费的，而把 query 花在真正重要的
决策上是一项被度量的技能。provider 的 harness 看到与默认 runner 相同的 budget
以及相同的无数字 error hint。

---

## 4. Level-B 如何工作：controller interface

如今具体的扩展点是 **`league/` controller interface**，同一个抽象让 human、
script、mock 或 LLM 都能占据 16 个 club seat 中的任意一个
（World = 1 division × 16 teams，即 16 个 club）。一个 seat 由 `SeatSpec`
（`SeatSpec`）描述，并由一个 controller 驱动：
`HumanTerminalController`、`LLMController`、`BaselineController`、
`ScriptController`、`MockLLMController`。

接入自己 harness 的 provider 实现同一个 controller contract：给定一个 stop
packet，做决策，通过 engine 的 `obs`/`actions` layer 调用 tool，最终 `advance`。
engine 调用 controller；controller 拥有推理。因为每个 controller 都走完全相同的
`obs`/`actions` 边界，自定义 harness 无法看到或做任何默认 runner 做不到的事。

> **Engine 状态（如实说明）：** controller interface、`obs`/`actions` 边界、
> tool 和 scorer 都已发布。engine 向锁定的 World = 1 division × 16 teams
> （16 个 club）的 world-size 改动**正在进行中**；在它落地之前，部分 code path
> 仍假设旧的 world 形状。

**MCP adapter（计划中）。** 面向外部 agent 的预期 turnkey 路径是一个薄
MCP server，它通过 Model Context Protocol 暴露相同的 tool surface，这样任何
支持 MCP 的 agent 无需写 Python 即可占据一个 seat。其设计是对现有
`obs`/`actions` tool 的 1:1 wrapper（没有新 capability，没有新信息）——它是一个
adapter，不是第二个 game。在它发布之前，Level-B 接入通过上面的 `league/`
controller interface。

---

## 5. Scoring 如何返回

每次 run 都会产出一份 result JSON（`results/<label>.json`），包含 composite
score 及其**三个透明 channel**——honours、club net-worth（value-added）、
squad value——每个都做过 log 压缩，外加 early-settlement 的 `rho` factor。公式和
常量在 `score/composite.py` 中公开，并在 `RULES_EXPORT.md` §6（win/lose）和
§7.1（weights）中注释。scoring 没有任何隐藏：provider 可以从存储的 channel
自行重算他们的 score。

对于 League/online run，每个 seat 都在相同基础上独立打分。

---

## 6. 三种模式（可比较性规则）

provider 可以提供自己的 harness **并仍然拿到一个可比较的 score**，但只能在
一种模式下——且只要固定的 surface 保持固定。经验法则：*你控制 agent 的推理；
你不碰 game。*

FM Bench 有三种模式：

- **Solo** —— 官方 1v15（一个被测 LLM vs 15 个 scripted 对手），通过
  **我们固定的 harness** 运行。喂给 **Solo leaderboard**。此处**禁止**
  自定义 harness。
- **Arena** —— 官方 16-LLM（16 个由 LLM 驱动的 seat 在一个 world
  中）。喂给 **Arena leaderboard**。此处**禁止**自定义 harness。
- **Open Track** —— self-serve 1v15，provider 在自己的环境中用**他们自己的
  harness** 运行 benchmark。这是**唯一**允许自定义 harness 的模式。
  self-reported，replay-verified。

两个官方模式（Solo、Arena）都**禁止**自定义 harness——harness
本身就是让它们的 score 可比较的一部分。只有 **Open Track** 允许 provider 自带
harness。

| 必须**固定**（否则 score 不可比较） | 可由 **provider 控制**（仅 Open Track） |
|---|---|
| engine version（commit + `params_hash`，盖在每个 result 上） | system prompt / 给 model 的 instruction |
| official seed set（对 leaderboards 保密） | 推理策略、thinking budget、self-critique |
| tool set + observation contract | memory 策略（如何使用 notebook/history） |
| scorer（`score_v0.2`） | multi-agent / committee 脚手架 |
| 官方模式的 decoding temperature | provider 端 caching、retries |

- **Solo / Arena（官方）** —— FM Bench 用 official prompt、
  固定 temperature、secret seed set、pinned engine version 以及**我们固定的
  harness**，通过 official runner 运行 model。跨 model 直接可比较。它们分别
  喂给 Solo leaderboard 和 Arena leaderboard。
- **Open Track** —— provider 在自己的环境中运行自己的 harness 并 self-report，
  提交一份签名的 action log（`actions.jsonl`），这样 FM Bench 可以**将它对
  pinned engine 回放**并确定性地验证 score（`seed + action log → bit-identical
  rerun`）。标记为 "self-reported, replay-verified"。这是唯一存在自定义
  prompt/harness 的模式。

engine 的确定性（见 `RULES_EXPORT.md` §8）正是让 Open Track 验证成为可能的
原因：一份提交的 action log 要么回放到声称的 final state hash，要么被拒绝。

---

## 7. Provider 不能做什么（以及为什么）

- **不能看到隐藏信息。** 真实 ability 永远不会越过 `obs` 边界；自定义 harness
  和其他所有人一样只拿到 band + confidence。
- **不能改变对手。** 在官方 1v15 模式中，15 个对手 club 是 scripted
  （`market_ai`），这样 model A 和 model B 面对完全相同的 world；LLM 驱动的对手
  保留给 Arena（16-LLM）模式——正是 scripted 对手才让两个 model 的
  分数具有可比性。
- **不能改动 scorer 或 engine。** 两者都被 pin 住并盖进 result；一份 Open Track
  提交只有在能回放时才被接受。

这些不是对聪明才智的限制——它们是让一个数字意味着"这个 model 把一家 club 管理
得好"、并且跨 provider 可比较的不变量。

---

## 另见

- `docs/RULES_EXPORT.md` —— 真实的 prompt、packet、tool schema、scoring 以及
  数值 param（interface spec）。
- `README.md` —— quickstart、评测协议、模式矩阵。
- `docs/REASONING_TIERS.md` —— 跨 provider 的推理档位对齐、
  scripted-vs-LLM 对手的抉择。
