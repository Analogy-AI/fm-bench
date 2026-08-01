# Scoring — `score_v0.2` (value-added)

The single source of truth is `score/composite.py`; this doc mirrors it in
human form. Scores are comparable **only within one track length** and one
calibration version (every result file is stamped with `engine_commit +
params_hash + score version`).

## Final score

```
S_raw = w_honors    · ln(1 + max(H,0) / h_div)                    ← honors
      + w_networth  · sign(VA) · ln(1 + |VA| / va_w_div)   ← net-worth VALUE ADDED (primary)
      + w_squadvalue· ln(1 + max(M, 0) / m_div)            ← squad value (stabilizer)

S_final = ρ · max(S_raw, 0) + min(S_raw, 0)
```

### Settlement discount ρ
```
ρ = 1                                  if the run completed the full track
ρ = rho_base · (min(t, T) / T)^rho_exp if settled early (sacked / insolvent)
                                       t = settle year, T = total years
```
ρ multiplies **only the positive part** — dying early can never shrink a
negative result, so there is no "get sacked to cap losses" arbitrage.

## Inputs

```
H  = Σ honors points over the WHOLE run (cumulative)

VA = W_real − W_baseline                                    (€M)
       W_real     = mean of the last `last_n_seasons` deflated net-worth snapshots
       W_baseline = day-0 net worth of the SAME seed's world
       deflate(x, year) = x / (1 + inflation)^(year − 1)

M  = mean of the last `last_n_seasons` deflated true squad values   (€M)
```

Honors point values (`params.yaml → score.honors`):

| event | points |
|---|---|
| T1_CHAMPION | +100 |
| T1_TOP4 | +20 |
| T2_CHAMPION | +30 |
| T2_PROMOTED | +15 |
| RELEGATED_T1 | −40 |

### Net worth
```
net_worth = cash
          + squad value      × squad_liquidity_discount
          + facilities       (under construction × construction_discount)
          − fee liabilities (outstanding transfer instalments)
where cash ABOVE cash_excess_wage_mult × annual wage bill counts only at
cash_excess_discount  (anti cash-hoarding).
```

## Parameters (`params.yaml → score`)

| symbol | meaning | value |
|---|---|---|
| `w_honors` | honors weight | 18 |
| `w_networth` | net-worth weight | 10 |
| `w_squadvalue` | squad weight | 6 |
| `h_div` | honors divisor | 60 |
| `va_w_div` | net-worth divisor | 40 |
| `m_div` | squad divisor | 80 |
| `last_n_seasons` | seasons averaged for W / M | 3 |
| `rho_base` / `rho_exp` | settlement discount base / exponent | 0.8 / 1.5 |
| `cash_excess_wage_mult` | "excess cash" threshold (× wage bill) | 3 |
| `cash_excess_discount` | excess-cash weight | 0.3 |
| `squad_liquidity_discount` | squad-value discount | 0.8 |
| `construction_discount` | in-progress facility discount | 0.5 |
| `inflation` (`economy.inflation_revenue`) | annual, for deflation | 0.04 |

## Display score (bounded presentation, official score unchanged)

Leaderboards additionally show `s_display = 100·S_final / (|S_final| + 25)`: a
monotonic, zero-preserving squash of the uncapped official score onto
(−100, 100) — asymptotic to ±100, never cut off, invertible
(`S = 25·d/(100−|d|)`). Rational saturation was chosen over `tanh` because
tanh approaches 100 exponentially fast: on 20-year tracks, where strong
runs reach `S_final` 80–100+, every top model displayed as 99.x. The
rational form approaches 100 polynomially, so top-end gaps stay visible at
any horizon (85 vs 105 → 77.3 vs 80.8, not 99.78 vs 99.96). Purely
cosmetic: `S_final` stays the official quantity and the tie-breaker. The
constant 25 is a display choice, deliberately not a `params.yaml` entry, so
it never perturbs `params_hash` comparability stamps. Reference points:
0 → 0, heuristic-grade (12) ≈ 33, oracle-grade (16) ≈ 39, sonnet-5's 5y
42.6 ≈ 63, S=110 ("god-tier" 20y) ≈ 81.

## Design properties (why it is shaped this way)

1. **Log-compressed, uncapped** — diminishing returns; no single channel can run away.
2. **VA on net worth, minus the day-0 baseline** — buying players cannot pump the
   score (the asset and the cash/fee cost net out), and per-seed endowment luck is
   subtracted. Calibration round 4: seed-luck fell from ~34% of score variance to
   ~13%, and skill rose to ~33%.
3. **Squad channel (reduced weight, absolute)** — net worth alone would reward a
   cash-hoarding / squad-stripping "miser": sell everyone, bank the money, high net
   worth, terrible team. The squad channel rewards actually fielding a valuable
   squad. Low weight + net-worth VA means it does **not** double-count trading profit.
4. **W / M use the last-3-season deflated mean** — not a final-day snapshot
   (which invites end-game fire-sale pumping) and not a 20-year average (which
   would reward early wealth later squandered). It measures where you left the
   club, sustainably.

## Two leaderboards (different layers)

- **Solo leaderboard** — Solo (1 tested LLM + 15 scripts): the
  **absolute** `S_final` above. Comparable across models because the scripted
  environment is identical for everyone.
- **Arena leaderboard** — Arena (16 seats; the published board ran 15 LLMs + 1 scripted anchor): a
  **relative** ranking (Elo + mean rank) aggregated over the per-seat `S_final`
  values across worlds. The Arena ranking code is part of the operated Arena
  layer and is not in this open release. League
  points and league position are **not** direct scoring channels; ranking is a
  property of this Arena layer, not of the composite.

---

# 评分 — `score_v0.2`(增值计分)

唯一真相源是 `score/composite.py`,本文是它的人类可读镜像。分数**只在同一赛道长度、同一校准版本内可比**(每个结果文件都盖了 `engine_commit + params_hash + score version` 戳)。

## 最终分

```
S_raw = w_honors    · ln(1 + max(H,0) / h_div)                    ← 荣誉
      + w_networth  · sign(VA) · ln(1 + |VA| / va_w_div)   ← 净值「增值」(主通道)
      + w_squadvalue· ln(1 + max(M, 0) / m_div)            ← 阵容价值(稳定项)

S_final = ρ · max(S_raw, 0) + min(S_raw, 0)
```

### 提前结算折扣 ρ
```
ρ = 1                                      打满全程
ρ = rho_base · (min(t, T) / T)^rho_exp     提前结算(被炒/破产),t=结算年,T=总年数
```
ρ **只乘正分** —— 提前死不会让负分缩小,杜绝"故意被炒止损"。

## 输入

```
H  = 全程累计荣誉点数

VA = W_real − W_baseline                                   (单位 €M)
       W_real     = 最后 last_n_seasons 季「平减净值」的均值
       W_baseline = 同 seed 世界第 0 天净值
       平减: deflate(x, year) = x / (1 + 通胀)^(year − 1)

M  = 最后 last_n_seasons 季「平减真实阵容价值」的均值        (单位 €M)
```

荣誉点数(`params.yaml → score.honors`):T1_CHAMPION +100 · T1_TOP4 +20 ·
T2_CHAMPION +30 · T2_PROMOTED +15 · RELEGATED_T1 −40。

### 净值
```
净值 = 现金
     + 阵容价值   × squad_liquidity_discount(0.8)
     + 设施价值   (在建 × construction_discount 0.5)
     − 转会费负债(欠款)
其中「超额现金」(超过 cash_excess_wage_mult=3 × 年薪总额的部分)只按
cash_excess_discount=0.3 计入(反囤现金)。
```

## 参数

| 符号 | 含义 | 值 |
|---|---|---|
| `w_honors` / `w_networth` / `w_squadvalue` | 三通道权重 | 18 / 10 / 6 |
| `h_div` / `va_w_div` / `m_div` | 三通道除数 | 60 / 40 / 80 |
| `last_n_seasons` | 财富/阵容取最后几季 | 3 |
| `rho_base` / `rho_exp` | 折扣基数 / 指数 | 0.8 / 1.5 |
| `inflation` | 年通胀(平减用) | 0.04 |

## 展示分(有界的呈现层,官方分不变)

榜单额外显示 `s_display = 100·S_final / (|S_final| + 25)`:把无封顶的官方分单调压缩到
(−100, 100)——渐近 ±100、永不截断、可逆(`S = 25·d/(100−|d|)`)、0 分保持 0
("原样交还俱乐部"语义不变)。选有理饱和而不是 tanh:tanh 以指数速度贴近 100,
20 年赛道上强模型的 S_final 达到 80–100+ 时全部挤在 99.x;有理式以多项式速度逼近,
任何年限下顶端差距都看得见(85 vs 105 → 77.3 vs 80.8,而不是 99.78 vs 99.96)。
纯展示用:`S_final` 仍是官方量与决胜依据。常数 25 是展示选择,刻意不进
`params.yaml`,以免扰动 `params_hash` 可比性戳。参照:纪律脚本档(12)≈33、
oracle 档(16)≈39、sonnet-5 五年局 42.6 ≈ 63、"神级"110 分 ≈ 81。

## 设计性质(为什么这么写)

1. **log 压缩、无上限**:边际递减,单通道不会爆分。
2. **VA 用净值 + 减第0天基线**:买人刷不了分(资产与现金/费用相抵),开局禀赋运气被扣掉(校准第4轮:运气占方差从 ~34% 降到 ~13%,skill 升到 ~33%)。
3. **阵容通道(低权重、绝对值)**:只算"荣誉+净值"会奖励"卖光囤现金"的守财奴;阵容通道奖励真在场上留一支有价值的队。低权重 + 净值 VA 使它**不**重复计交易利润。
4. **W/M 取最后 3 季平减均值**:不是最后一天快照(会被终局甩卖刷分),也不是 20 年平均(会奖励早富后败);衡量"你把俱乐部可持续地留在什么状态"。

## 两个榜(不同层)

- **Solo 榜** —— Solo(1 被测 LLM + 15 脚本):上面的**绝对** `S_final`,因环境固定而跨模型可比。
- **Arena 榜** —— Arena(16 个 seat;已公布榜单为 15 LLM + 1 脚本锚点):在各席位 `S_final` 之上做的**相对**排名(Elo + 平均名次;Arena 排名代码属于我们运营的 Arena 层,不在本次开源发布内)。联赛积分与名次**不是**直接评分通道;排名是对战层的属性,不在 composite 里。
