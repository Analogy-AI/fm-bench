# Reasoning tiers — what every seat runs, and why

Policy (owner decision, 2026-07-14): every roster seat runs at the
**provider-recommended / documented-default** reasoning tier for agentic
tool use. No per-model tuning by us. The EFFECTIVE tier is stamped into
every run (`provider_adapter`, e.g. `anthropic-messages:adaptive`,
`openai-responses:medium`, `together-chat:reasoning-on`) — manifests,
per-seed results, and summaries all carry it, and **pools with different
stamps never merge**. Env overrides exist for experiments and are also
reflected in the stamp.

Verified 2026-07-14 (audit with live pings; three seats flagged for
re-verification below). Re-check this table against provider docs on campaign-launch day: provider
defaults move, and a stale row silently changes what "same effort" means.

| seat | tier we run | provider recommendation / default | stamp | override env |
|---|---|---|---|---|
| claude-fable-5 | thinking always-on (no param sent) | Anthropic: Fable has thinking always on; param rejected | `anthropic-messages:always-on` | — |
| claude-opus-4-8 | `thinking: {type: adaptive}` | Anthropic-recommended for 4.6+ models | `anthropic-messages:adaptive` | — |
| claude-sonnet-5 | `thinking: {type: adaptive}` | same | `anthropic-messages:adaptive` | — |
| claude-haiku-4-5 | extended thinking, budget = max(1024, max_tokens), interleaved beta | only way to enable thinking on 4.5-era models | `anthropic-messages:budget` | — |
| gpt-5.6-sol / -terra | Responses API, `reasoning.effort = medium` | OpenAI API default effort | `openai-responses:medium` | `FMBENCH_OPENAI_REASONING_EFFORT` |
| gemini-3.5-flash | `thinkingConfig.thinkingLevel = medium` | Google docs default (pinned explicitly) | `google-genai:thinking-medium` | `FMBENCH_GEMINI_THINKING_LEVEL` |
| gemini-3-flash-preview | `thinkingLevel = high` | ⚠ re-verify vs docs before campaign | `google-genai:thinking-high` | `FMBENCH_GEMINI_THINKING_LEVEL` |
| grok-4.5 | nothing sent | xAI: reasoning built-in, no accepted knob | `openai-chat:reasoning-builtin` | — |
| deepseek-ai/DeepSeek-V4-Pro | `reasoning.enabled = true` (extra_body) | hybrid family, serving default ON — pinned so drift can't change what a run measured | `openai-chat:reasoning-on` | — |
| moonshotai/Kimi-K2.6 | same | same (reasoning capability confirmed live: 130 reasoning tokens) | `openai-chat:reasoning-on` | — |
| Qwen/Qwen3.7-Max | same (streamed path accumulates reasoning deltas) | same | `openai-chat:reasoning-on` | — |
| zai-org/GLM-5.2 | same | same | `openai-chat:reasoning-on` | — |
| MiniMaxAI/MiniMax-M3 | same (wired 2026-07-15) | M-series is a reasoning family; Together serving default ON — ⚠ confirm reasoning tokens at pre-campaign ping | `openai-chat:reasoning-on` | — |
| Muse-Spark-1.1 | Responses API on api.meta.ai, `reasoning.effort = high` | ⚠ re-verify Meta's agentic recommendation before campaign (launch-doc reading; encrypted reasoning replay verified live) | `openai-responses:high` | `FMBENCH_META_REASONING_EFFORT`, `FMBENCH_META_CHAT=1` (legacy path) |

Notes
- Self-heals re-stamp honestly (e.g. a server rejecting thinking →
  `:none-healed`), so a degraded run can never masquerade as full-power.
- Old pools (pre 2026-07-14, thinking largely off) carry the old stamps and
  are labeled legacy in the paper; they never mix with reasoning-on pools.
- The three ⚠ seats are non-blocking for correctness (settings are at or
  above default) but should be re-checked against primary docs on launch
  day so the "provider-recommended" claim is airtight.

---

# 推理档位——每席跑什么、依据是什么(中文)

**政策(2026-07-14 定)**:所有席位一律使用**厂商推荐/文档默认**的推理
档位,我们不做逐模型调参。每局的实际档位盖章进结果
(`provider_adapter` 戳,如 `anthropic-messages:adaptive`)——manifest、
逐 seed 结果、汇总三处都有;**不同戳的池永不合并**。实验用的环境变量
口子存在,改了也会如实进戳。

各席明细见上表(英文)。要点:

- Anthropic:fable 强制思考(不发参数);opus/sonnet 用官方推荐的
  `adaptive`;haiku 系只能用 budget 方式开启思考
- OpenAI gpt-5.6:Responses API,`effort=medium`(API 默认)
- Gemini:显式钉住文档默认档(flash=medium;preview=high ⚠ 发车前复核)
- grok-4.5:推理内建常开,无旋钮
- Together 四家(DeepSeek/Kimi/Qwen/GLM)+ **MiniMax-M3(2026-07-15
  接线)**:显式 `reasoning.enabled=true` 钉住"默认开",防服务端默认漂移
- Muse:api.meta.ai Responses,`effort=high`(⚠ 发车前复核 Meta 推荐)

自愈降级会如实改戳(如 `:none-healed`),降级局绝不冒充满血局;
2026-07-14 之前的旧池(基本未开思考)带旧戳,论文中已标注 legacy,
与新池永不混。⚠ 三席需在发车日对照 provider 官方文档复核。
