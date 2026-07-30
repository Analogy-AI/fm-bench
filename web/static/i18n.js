/* Bilingual (EN / 中文) UI layer for the human web client.
 *
 * Human-facing only: the game engine, tools, and packets are unchanged, and the
 * LLM agent path never loads this (agents are natively multilingual; rules.md
 * stays the English canonical prompt). This just localizes the human UI chrome.
 *
 * Usage:
 *   - Static markup: add data-i18n="key" (textContent) or data-i18n-html="key"
 *     (innerHTML, for strings containing markup). applyI18n() fills them in.
 *   - Dynamic JS strings: call t("key", {name: value}) — {name} placeholders are
 *     substituted. Missing keys fall back to the key itself, so untranslated
 *     strings are visible rather than blank, and coverage can grow incrementally.
 *
 * Language is persisted in localStorage("fmbench.lang"); default follows the
 * browser, falling back to English.
 */
(function () {
  const STR = {
    en: {
      "app.title": "FM Bench",
      "lang.toggle": "中文",
      "ng.title": '<span class="accent">⚽ FM Bench</span> — take the job',
      "ng.lead": "You manage the club. The engine simulates the world day by day and stops when something needs your decision. Same rules, same tools, same information as the AI agents get.",
      "ng.seed": "Seed",
      "ng.years": "Years",
      "ng.years5": "5 (short track)",
      "ng.years20": "20 (full career)",
      "ng.pitchNote": "Player ability is never shown exactly — you get scouted <b>ranges</b> with a confidence grade. The board expects results <i>and</i> financial discipline; fail both and you're sacked, which settles your score early at a steep discount.",
      "ng.start": "Start career",
      "so.title": "Season review",
      "so.close": "On to the new season ▶",
      "go.title": "Career over",
      "go.new": "New career",
      "hdr.date": "date",
      "hdr.position": "position",
      "hdr.cash": "cash",
      "hdr.board": "board",
      "hdr.budget": "queries · offers",
      "hdr.continue": "Continue ▶",
      "inbox.title": "Inbox",
      "continue.hint": 'The engine simulates until the next decision stop — exactly what an agent gets from <span style="font-family:var(--mono)">advance()</span>. Shortcut: <kbd>c</kbd>',
      "tab.squad": "Squad",
      "tab.tactics": "Tactics",
      "tab.table": "Table",
      "tab.fixtures": "Fixtures",
      "tab.market": "Market",
      "tab.finances": "Finances",
      "tab.club": "Club",
      "tab.youth": "Youth",
      "tab.history": "History",
      "tab.notebook": "Notebook",
      "foot.continue": "continue",
      "foot.tabs": "switch tabs",
      "foot.tools": "Every button here is one of the same tools AI agents call.",

      // --- dynamic JS-rendered strings (app.js) ---
      "banner.dismiss": "dismiss",

      // header / since-last-stop
      "header.sub": "Division {division} · target top {target} · year {year}",
      "since.title": "Since your last stop",
      "since.points": "points",
      "since.places": "places",
      "since.cash": "cash",
      "since.board": "board",

      // inbox
      "inbox.titleFull": "Inbox — stop {stop} ({types})",
      "inbox.windowOpen": "{window} window open",
      "inbox.injured": "{n} injured",
      "inbox.next": "next: {loc} {opponent} (d{day})",
      "inbox.negotiations": "Negotiations",
      "inbox.events": "Events",
      "inbox.quiet": "Quiet spell — nothing needs you. Press Continue.",
      "tag.action": "action",
      "tag.info": "info",

      // inbox event types
      "evt.transferOffer": "Transfer offer",
      "evt.transferCompleted": "Transfer completed",
      "evt.offerExpired": "Offer expired",
      "evt.offerAutoRejected": "Offer auto-rejected",
      "evt.boardWarning": "Board warning",
      "evt.majorInjury": "Major injury",
      "evt.contractExpiring": "Contract expiring",
      "evt.youthIntake": "Youth intake",
      "evt.facilityComplete": "Facility completed",
      "evt.playerLeftFree": "Player left (free)",
      "evt.playerRetired": "Player retired",
      "evt.administration": "Administration",

      // negotiations / offers
      "offer.accept": "Accept",
      "offer.reject": "Reject",
      "offer.counter": "Counter",
      "offer.acceptCounter": "Accept counter {amount}",
      "offer.recounter": "Re-counter",
      "offer.withdraw": "Withdraw",
      "offer.empty": "No live negotiations.",
      "offer.selling": "selling",
      "offer.buying": "buying",
      "offer.from": "from",
      "offer.to": "to",
      "offer.expiresIn": "expires in {days}d",
      "offer.theirCounter": "their counter: {amount}",

      // toasts
      "toast.sold": "Sold",
      "toast.rejected": "Rejected",
      "toast.countered": "Countered",
      "toast.bought": "Bought",
      "toast.withdrawn": "Withdrawn",
      "toast.listed": "Listed for transfer",
      "toast.unlisted": "Removed from list",
      "toast.released": "Released (severance {amount})",
      "toast.promoted": "Promoted to senior squad",
      "toast.signed": "Signed!",
      "toast.wantsMore": "Wants more",
      "toast.wantsMoreWage": "Wants more: {amount}/y",
      "toast.offer": "Offer {status}",
      "toast.offerCounter": " — counter {amount}",
      "toast.contractAgreed": "Contract agreed",
      "toast.contractRejected": "Rejected",
      "toast.contractWantsWage": " — wants {amount}/y",
      "toast.noCid": "Could not resolve club id for the report.",
      "toast.lineupSet": "Lineup set — it will be honored.",
      "toast.tactics": "Tactics: {formation} {style}",
      "toast.investStarted": "{kind} upgrade started — {cost}, {days} days",
      "toast.notebookSaved": "Notebook saved ({used}/{cap} chars)",

      // row actions & inputs
      "action.view": "view",
      "action.unlist": "unlist",
      "action.list": "list",
      "action.release": "release",
      "action.promote": "promote",
      "action.sign": "sign",
      "action.bid": "bid",
      "action.close": "✕ close",
      "action.offerContract": "Offer contract",
      "action.apply": "Apply",
      "action.report": "report",
      "action.refresh": "↻ refresh (1 query)",
      "badge.listed": "listed",
      "confirm.release": "Release {name}? Severance: half a year's wage.",
      "ph.wage": "wage €M/y",
      "ph.fee": "fee €M",

      // player detail
      "detail.club": "Club",
      "detail.freeAgent": "free agent",
      "detail.ability": "Ability",
      "detail.form": "Form",
      "detail.contract": "Contract",
      "detail.monthsLeft": "{months} months left",
      "detail.thisSeason": "This season",
      "detail.seasonStats": "{apps} apps · {goals}g {assists}a · {minutes} min",
      "detail.fitness": "Fitness",
      "detail.injured": "injured, {days} days out",
      "detail.fit": "fit",
      "detail.wage": "Wage",
      "detail.morale": "Morale / fatigue",
      "detail.potential": "Potential",
      "detail.renew": "Renew:",

      // match report
      "report.title": "{home} {hg}–{ag} {away} · round {round}",

      // column headers
      "col.player": "Player",
      "col.pos": "Pos",
      "col.age": "Age",
      "col.ability": "Ability",
      "col.form": "Form",
      "col.wage": "Wage",
      "col.deal": "Deal",
      "col.season": "Season",
      "col.actions": "Actions",
      "col.rating": "Rating",
      "col.goals": "Goals",
      "col.assists": "Assists",
      "col.rank": "#",
      "col.club": "Club",
      "col.p": "P",
      "col.w": "W",
      "col.d": "D",
      "col.l": "L",
      "col.gd": "GD",
      "col.pts": "Pts",
      "col.rd": "Rd",
      "col.day": "Day",
      "col.ha": "H/A",
      "col.opponent": "Opponent",
      "col.result": "Result",
      "col.status": "Status",
      "col.potential": "Potential",

      // squad tab
      "squad.setLineup": "Set lineup ({n}/11)",
      "squad.hint": "Tick exactly 11 · injured players can't start · minutes drive development",

      // tactics tab
      "tactics.style.gegenpress": "gegenpress",
      "tactics.style.lowblock": "lowblock",
      "tactics.style.possession": "possession",
      "tactics.formation": "Formation",
      "tactics.styleLabel": "Style",
      "tactics.note": "Style changes take effect from the next round. Matchups matter, but squad strength dominates — tactics are a tiebreaker, not a cheat code.",
      "tactics.currentSetup": "Current setup",
      "tactics.stadium": "Stadium",
      "tactics.seats": "{n} seats",

      // table tab
      "table.legend1": "Gold stripe: title · red: relegation zone.",
      "table.legend2": "Gold stripe: title · green: promotion places · red: drop zone.",

      // fixtures tab
      "fixtures.form": "Form (last 5): ",
      "fixtures.h": "H",
      "fixtures.a": "A",

      // market tab
      "market.note": "Listed players, free agents and expiring contracts. Bids resolve within this stop (counters appear under Negotiations). Offers are metered — probing is not free.",
      "market.empty": "Market is empty right now.",
      "market.status.free_agent": "free agent",
      "market.status.listed": "listed",
      "market.status.contract_expiring": "contract expiring",
      "market.status.transfer_listed": "transfer listed",
      "market.status.loan_listed": "loan listed",

      // finances tab
      "fin.cash": "Cash",
      "fin.revLast": "Revenue (last season)",
      "fin.revYtd": "Revenue (season to date)",
      "fin.costYtd": "Costs (season to date)",
      "fin.wageBill": "Annual wage bill",
      "fin.parachute": "Parachute years left",
      "fin.inAdmin": "In administration",
      "fin.yes": "YES",
      "fin.wageRatio": "Wage / revenue ratio (healthy band 55–70%)",
      "fin.warnLine": "board warning line",
      "fin.wagesVsRev": "Wages vs last season's revenue",
      "fin.cashAcross": "cash across your {n} stops",
      "fin.note": "Overspend compounds; administration is reachable and it ends your career.",

      // club tab
      "club.division": "Division",
      "club.position": "League position",
      "club.boardConf": "Board confidence",
      "club.seasonTarget": "Season target",
      "club.top": "top {n}",
      "club.reputation": "Reputation",
      "club.stadiumCap": "Stadium capacity",
      "club.academy": "Academy level",
      "club.training": "Training level",
      "club.projects": "Projects",
      "club.project": "{kind} ({days}d left)",
      "club.none": "none",
      "club.kind.academy": "academy",
      "club.kind.training": "training",
      "club.kind.stadium": "stadium",
      "club.invest": "Invest: {kind}",
      "club.note": "Investments pay back years later. That's the point.",

      // youth tab
      "youth.empty": "No academy prospects right now — intake arrives each season.",
      "youth.note": "Potential stars are the scouts' guess, not a promise. Promoted kids grow with minutes.",

      // history tab
      "history.honors": "honors",
      "history.seasons": "seasons",
      "history.transfers": "transfers",
      "history.empty": "Nothing yet.",

      // notebook tab
      "nb.chars": "{n} chars",
      "nb.charsCap": "{used}/{cap} chars",
      "nb.save": "Save notebook",
      "nb.note": "Your cross-season memory. Agents get this exact mechanism — a size-capped notebook carried to every stop. Deciding what to keep is part of the game.",

      // season review overlay
      "review.finished": "Finished",
      "review.finishedVal": "#{pos} ({points} pts)",
      "review.targetWas": "Season target was",
      "review.cashEnd": "Cash at season end",
      "review.boardConf": "Board confidence",
      "review.cashThrough": "cash through the season",
      "review.note": "Full tables and honors live in the History tab. New season targets are set — check the board's mood before you spend.",

      // game-over overlay
      "gameover.complete": "Career complete",
      "gameover.sacked": "You were sacked",
      "gameover.admin": "Administration — the money ran out",
      "gameover.over": "Career over",
      "gameover.composite": "composite score",
      "gameover.reason.completed": "completed",
      "gameover.reason.fired": "fired",
      "gameover.reason.administration": "administration",
      "gameover.settledPrefix": "Settled ",
      "gameover.atYear": " at year {t}/{years} · ",
      "gameover.stops": " · {stops} decision stops · seed {seed} (replayable)",
      "gameover.chanHonors": "Honors ({pts} pts)",
      "gameover.chanNetWorth": "Net worth ({val})",
      "gameover.chanSquadValue": "Squad value ({val})",
      "gameover.whereLand": "Where you land",
      "gameover.ladder.random": "random clicks",
      "gameover.ladder.reckless": "reckless spender",
      "gameover.ladder.scripted": "scripted manager",
      "gameover.ladder.oracle": "all-knowing oracle",
      "gameover.ladder.you": "you",
      "gameover.ladderNote": "Baselines: calibration-bot means on the 5-year track under value-added scoring (v0.3 constants — they move as the game is tuned). Zero ≈ leaving the club as valuable as you found it.",
      "gameover.honors": "Honors",

      // --- squad showcase page (showcase.html / showcase.js) ---
      "sc.pageTitle": "FM Bench — Squad Showcase",
      "sc.title": '<span class="brand">⚽ FM Bench</span> — Squad Showcase',
      "sc.sub": "A procedurally-generated fictional club, presented in full.",
      "sc.load": "Load squad",
      "sc.back": "← Back to game",
      "sc.bestXI": "Best XI",
      "sc.bestXIHint": "auto-selected by ability band × fitness",
      "sc.fullSquad": "Full squad",
      "sc.fullSquadHint": "ability is always a range + confidence — true skill is never revealed",
      "sc.srcLive": "live engine data",
      "sc.srcSample": "sample (server offline)",
      "sc.badgeInjured": "injured",
      "sc.bandTitle": "ability range (confidence {conf})",
      "sc.formTitle": "recent form",
      "sc.spotTitle": "{name} ({position}) · band {low}-{high}",
      "sc.unknownClub": "Unknown FC",
      "sc.teamMeta": "Division {division} · seed {seed} · style {style}",
      "sc.position": "Position",
      "sc.board": "Board",
      "sc.noteLive": "Rendered {n} players from the live engine (seed {seed}). Ability is a range + confidence letter (A = own squad, B = scouted, C = public) — the true rating is never shown, to anyone.",
      "sc.noteSample": "Server offline — showing an embedded sample squad. Start run_web.py (localhost:8777) and reload to render a real procedurally-generated club from the engine.",
    },
    zh: {
      "app.title": "FM Bench",
      "lang.toggle": "EN",
      "ng.title": '<span class="accent">⚽ FM Bench</span> —— 接手球队',
      "ng.lead": "你来经营这家俱乐部。引擎逐日模拟整个世界，当有需要你决策的事情时就会暂停。规则、工具、信息都与 AI agent 完全一致。",
      "ng.seed": "随机种子",
      "ng.years": "年限",
      "ng.years5": "5 年（短赛道）",
      "ng.years20": "20 年（完整生涯）",
      "ng.pitchNote": "球员能力从不精确公开 —— 你只会得到带信心等级的球探<b>区间</b>评估。董事会既要成绩<i>也要</i>财务纪律；两者都做不到就会被解雇，届时分数提前结算并大幅折扣。",
      "ng.start": "开始生涯",
      "so.title": "赛季回顾",
      "so.close": "进入新赛季 ▶",
      "go.title": "生涯结束",
      "go.new": "新生涯",
      "hdr.date": "日期",
      "hdr.position": "排名",
      "hdr.cash": "现金",
      "hdr.board": "董事会",
      "hdr.budget": "查询 · 报价",
      "hdr.continue": "继续 ▶",
      "inbox.title": "收件箱",
      "continue.hint": '引擎会一直模拟到下一个决策停顿点 —— 与 agent 调用 <span style="font-family:var(--mono)">advance()</span> 得到的完全一样。快捷键：<kbd>c</kbd>',
      "tab.squad": "阵容",
      "tab.tactics": "战术",
      "tab.table": "积分榜",
      "tab.fixtures": "赛程",
      "tab.market": "转会市场",
      "tab.finances": "财务",
      "tab.club": "俱乐部",
      "tab.youth": "青训",
      "tab.history": "历史",
      "tab.notebook": "笔记本",
      "foot.continue": "继续",
      "foot.tabs": "切换标签页",
      "foot.tools": "这里的每个按钮都是 AI agent 调用的同一套工具之一。",

      // --- dynamic JS-rendered strings (app.js) ---
      "banner.dismiss": "关闭",

      // header / since-last-stop
      "header.sub": "级别 {division} · 目标前 {target} 名 · 第 {year} 年",
      "since.title": "自你上次停顿以来",
      "since.points": "积分",
      "since.places": "排名",
      "since.cash": "现金",
      "since.board": "董事会",

      // inbox
      "inbox.titleFull": "收件箱 —— 停顿 {stop}（{types}）",
      "inbox.windowOpen": "{window}转会窗开启",
      "inbox.injured": "{n} 人受伤",
      "inbox.next": "下一场：{loc} {opponent}（第{day}天）",
      "inbox.negotiations": "谈判",
      "inbox.events": "事件",
      "inbox.quiet": "风平浪静 —— 暂无需你处理的事务。点击“继续”。",
      "tag.action": "待处理",
      "tag.info": "通知",

      // inbox event types
      "evt.transferOffer": "转会报价",
      "evt.transferCompleted": "转会完成",
      "evt.offerExpired": "报价过期",
      "evt.offerAutoRejected": "报价被自动拒绝",
      "evt.boardWarning": "董事会警告",
      "evt.majorInjury": "重大伤病",
      "evt.contractExpiring": "合同即将到期",
      "evt.youthIntake": "青训新秀加入",
      "evt.facilityComplete": "设施建成",
      "evt.playerLeftFree": "球员自由离队",
      "evt.playerRetired": "球员退役",
      "evt.administration": "破产托管",

      // negotiations / offers
      "offer.accept": "接受",
      "offer.reject": "拒绝",
      "offer.counter": "还价",
      "offer.acceptCounter": "接受还价 {amount}",
      "offer.recounter": "再还价",
      "offer.withdraw": "撤回",
      "offer.empty": "暂无进行中的谈判。",
      "offer.selling": "出售",
      "offer.buying": "求购",
      "offer.from": "来自",
      "offer.to": "发往",
      "offer.expiresIn": "{days} 天后到期",
      "offer.theirCounter": "对方还价：{amount}",

      // toasts
      "toast.sold": "已售出",
      "toast.rejected": "已拒绝",
      "toast.countered": "已还价",
      "toast.bought": "已购入",
      "toast.withdrawn": "已撤回",
      "toast.listed": "已挂牌转会",
      "toast.unlisted": "已撤下挂牌",
      "toast.released": "已解约（遣散费 {amount}）",
      "toast.promoted": "已提拔至一线队",
      "toast.signed": "签约成功！",
      "toast.wantsMore": "要价更高",
      "toast.wantsMoreWage": "要价更高：{amount}/年",
      "toast.offer": "报价 {status}",
      "toast.offerCounter": " —— 还价 {amount}",
      "toast.contractAgreed": "合同已达成",
      "toast.contractRejected": "已拒绝",
      "toast.contractWantsWage": " —— 期望 {amount}/年",
      "toast.noCid": "无法确定用于比赛报告的俱乐部 ID。",
      "toast.lineupSet": "阵容已设置 —— 将会被采用。",
      "toast.tactics": "战术：{formation} {style}",
      "toast.investStarted": "{kind}升级已启动 —— {cost}，{days} 天",
      "toast.notebookSaved": "笔记本已保存（{used}/{cap} 字）",

      // row actions & inputs
      "action.view": "查看",
      "action.unlist": "撤牌",
      "action.list": "挂牌",
      "action.release": "解约",
      "action.promote": "提拔",
      "action.sign": "签约",
      "action.bid": "报价",
      "action.close": "✕ 关闭",
      "action.offerContract": "提供合同",
      "action.apply": "应用",
      "action.report": "报告",
      "action.refresh": "↻ 刷新（消耗1次查询）",
      "badge.listed": "挂牌",
      "confirm.release": "解约 {name}？遣散费：半年薪资。",
      "ph.wage": "薪资 €M/年",
      "ph.fee": "转会费 €M",

      // player detail
      "detail.club": "俱乐部",
      "detail.freeAgent": "自由球员",
      "detail.ability": "能力",
      "detail.form": "状态",
      "detail.contract": "合同",
      "detail.monthsLeft": "剩余 {months} 个月",
      "detail.thisSeason": "本赛季",
      "detail.seasonStats": "{apps} 场 · {goals}球 {assists}助 · {minutes} 分钟",
      "detail.fitness": "健康状况",
      "detail.injured": "受伤，需 {days} 天恢复",
      "detail.fit": "健康",
      "detail.wage": "薪资",
      "detail.morale": "士气 / 疲劳",
      "detail.potential": "潜力",
      "detail.renew": "续约：",

      // match report
      "report.title": "{home} {hg}–{ag} {away} · 第{round}轮",

      // column headers
      "col.player": "球员",
      "col.pos": "位置",
      "col.age": "年龄",
      "col.ability": "能力",
      "col.form": "状态",
      "col.wage": "薪资",
      "col.deal": "合同",
      "col.season": "赛季",
      "col.actions": "操作",
      "col.rating": "评分",
      "col.goals": "进球",
      "col.assists": "助攻",
      "col.rank": "名次",
      "col.club": "俱乐部",
      "col.p": "场",
      "col.w": "胜",
      "col.d": "平",
      "col.l": "负",
      "col.gd": "净胜",
      "col.pts": "积分",
      "col.rd": "轮",
      "col.day": "天",
      "col.ha": "主/客",
      "col.opponent": "对手",
      "col.result": "比分",
      "col.status": "状态",
      "col.potential": "潜力",

      // squad tab
      "squad.setLineup": "设置首发（{n}/11）",
      "squad.hint": "勾选正好11人 · 伤病球员无法首发 · 出场时间促进成长",

      // tactics tab
      "tactics.style.gegenpress": "高位逼抢",
      "tactics.style.lowblock": "低位防守",
      "tactics.style.possession": "控球",
      "tactics.formation": "阵型",
      "tactics.styleLabel": "风格",
      "tactics.note": "风格调整从下一轮开始生效。对阵有影响，但阵容实力才是决定性因素 —— 战术只是打破平衡的筹码，不是作弊码。",
      "tactics.currentSetup": "当前配置",
      "tactics.stadium": "球场",
      "tactics.seats": "{n} 个座位",

      // table tab
      "table.legend1": "金色条纹：冠军 · 红色：降级区。",
      "table.legend2": "金色条纹：冠军 · 绿色：升级名额 · 红色：降级区。",

      // fixtures tab
      "fixtures.form": "近期状态（最近5场）：",
      "fixtures.h": "主",
      "fixtures.a": "客",

      // market tab
      "market.note": "挂牌球员、自由球员及即将到期的合同。报价在本次停顿内结算（还价会出现在“谈判”中）。报价有次数限制 —— 试探并非免费。",
      "market.empty": "目前转会市场空无一人。",
      "market.status.free_agent": "自由球员",
      "market.status.listed": "挂牌",
      "market.status.contract_expiring": "合同到期",
      "market.status.transfer_listed": "转会挂牌",
      "market.status.loan_listed": "租借挂牌",

      // finances tab
      "fin.cash": "现金",
      "fin.revLast": "收入（上赛季）",
      "fin.revYtd": "收入（本赛季至今）",
      "fin.costYtd": "支出（本赛季至今）",
      "fin.wageBill": "年度薪资总额",
      "fin.parachute": "降落伞金剩余年数",
      "fin.inAdmin": "破产托管中",
      "fin.yes": "是",
      "fin.wageRatio": "薪资 / 收入比（健康区间 55–70%）",
      "fin.warnLine": "董事会警戒线",
      "fin.wagesVsRev": "薪资 vs 上赛季收入",
      "fin.cashAcross": "你 {n} 次停顿的现金变化",
      "fin.note": "超支会不断累积；破产托管并非遥不可及，而它会终结你的生涯。",

      // club tab
      "club.division": "级别",
      "club.position": "联赛排名",
      "club.boardConf": "董事会信心",
      "club.seasonTarget": "赛季目标",
      "club.top": "前 {n} 名",
      "club.reputation": "声望",
      "club.stadiumCap": "球场容量",
      "club.academy": "青训营等级",
      "club.training": "训练设施等级",
      "club.projects": "在建项目",
      "club.project": "{kind}（剩余{days}天）",
      "club.none": "无",
      "club.kind.academy": "青训营",
      "club.kind.training": "训练设施",
      "club.kind.stadium": "球场",
      "club.invest": "投资：{kind}",
      "club.note": "投资要多年后才有回报。这正是意义所在。",

      // youth tab
      "youth.empty": "目前青训营没有新秀 —— 每个赛季都会有新一批学员加入。",
      "youth.note": "潜力星级是球探的估计，并非承诺。被提拔的年轻人靠出场时间成长。",

      // history tab
      "history.honors": "荣誉",
      "history.seasons": "赛季",
      "history.transfers": "转会",
      "history.empty": "暂无记录。",

      // notebook tab
      "nb.chars": "{n} 字",
      "nb.charsCap": "{used}/{cap} 字",
      "nb.save": "保存笔记本",
      "nb.note": "你的跨赛季记忆。agent 得到的正是这套机制 —— 一个有容量上限、带到每次停顿的笔记本。决定保留什么本身就是游戏的一部分。",

      // season review overlay
      "review.finished": "最终排名",
      "review.finishedVal": "第{pos}名（{points} 分）",
      "review.targetWas": "赛季目标为",
      "review.cashEnd": "赛季末现金",
      "review.boardConf": "董事会信心",
      "review.cashThrough": "整个赛季的现金变化",
      "review.note": "完整积分榜和荣誉在“历史”标签页。新赛季目标已设定 —— 花钱前先看看董事会的态度。",

      // game-over overlay
      "gameover.complete": "生涯圆满结束",
      "gameover.sacked": "你被解雇了",
      "gameover.admin": "破产托管 —— 钱花光了",
      "gameover.over": "生涯结束",
      "gameover.composite": "综合得分",
      "gameover.reason.completed": "圆满完成",
      "gameover.reason.fired": "被解雇",
      "gameover.reason.administration": "破产托管",
      "gameover.settledPrefix": "结算于 ",
      "gameover.atYear": " 第 {t}/{years} 年 · ",
      "gameover.stops": " · {stops} 次决策停顿 · 种子 {seed}（可重玩）",
      "gameover.chanHonors": "荣誉（{pts} 分）",
      "gameover.chanNetWorth": "净资产（{val}）",
      "gameover.chanSquadValue": "阵容价值（{val}）",
      "gameover.whereLand": "你的定位",
      "gameover.ladder.random": "随机乱点",
      "gameover.ladder.reckless": "挥霍无度者",
      "gameover.ladder.scripted": "脚本经理",
      "gameover.ladder.oracle": "全知先知",
      "gameover.ladder.you": "你",
      "gameover.ladderNote": "基准线：5年赛道上按增值计分法的校准 bot 均值（v0.3 常数 —— 会随游戏调校而变动）。零 ≈ 让俱乐部离开时的价值与你接手时相当。",
      "gameover.honors": "荣誉",

      // --- squad showcase page (showcase.html / showcase.js) ---
      "sc.pageTitle": "FM Bench —— 阵容展示",
      "sc.title": '<span class="brand">⚽ FM Bench</span> —— 阵容展示',
      "sc.sub": "一支程序化生成的虚构俱乐部，完整呈现。",
      "sc.load": "加载阵容",
      "sc.back": "← 返回游戏",
      "sc.bestXI": "最佳首发11人",
      "sc.bestXIHint": "按能力区间 × 健康状况自动选出",
      "sc.fullSquad": "完整阵容",
      "sc.fullSquadHint": "能力始终是区间 + 信心等级 —— 真实实力从不公开",
      "sc.srcLive": "实时引擎数据",
      "sc.srcSample": "示例数据（服务器离线）",
      "sc.badgeInjured": "受伤",
      "sc.bandTitle": "能力区间（信心等级 {conf}）",
      "sc.formTitle": "近期状态",
      "sc.spotTitle": "{name}（{position}） · 区间 {low}-{high}",
      "sc.unknownClub": "未知俱乐部",
      "sc.teamMeta": "级别 {division} · 种子 {seed} · 风格 {style}",
      "sc.position": "排名",
      "sc.board": "董事会",
      "sc.noteLive": "已从实时引擎渲染 {n} 名球员（种子 {seed}）。能力以区间 + 信心字母呈现（A = 自家阵容，B = 已球探，C = 公开信息）—— 真实评分从不向任何人公开。",
      "sc.noteSample": "服务器离线 —— 正在显示内置的示例阵容。启动 run_web.py（localhost:8777）并重新加载，即可渲染引擎程序化生成的真实俱乐部。",
    },
  };

  const SUPPORTED = Object.keys(STR);

  function detect() {
    const saved = localStorage.getItem("fmbench.lang");
    if (saved && SUPPORTED.includes(saved)) return saved;
    const nav = (navigator.language || "en").toLowerCase();
    return nav.startsWith("zh") ? "zh" : "en";
  }

  let lang = detect();

  function t(key, vars) {
    let s = (STR[lang] && STR[lang][key]) || (STR.en && STR.en[key]) || key;
    if (vars) {
      for (const k in vars) s = s.replace(new RegExp("\\{" + k + "\\}", "g"), vars[k]);
    }
    return s;
  }

  function applyI18n(root) {
    root = root || document;
    root.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    root.querySelectorAll("[data-i18n-html]").forEach((el) => {
      el.innerHTML = t(el.getAttribute("data-i18n-html"));
    });
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }

  function setLang(next) {
    if (!SUPPORTED.includes(next)) return;
    lang = next;
    localStorage.setItem("fmbench.lang", lang);
    applyI18n();
    document.dispatchEvent(new CustomEvent("i18n:changed", { detail: { lang } }));
  }

  function toggle() {
    setLang(lang === "en" ? "zh" : "en");
  }

  // expose
  window.I18N = { t, applyI18n, setLang, toggle, get lang() { return lang; }, SUPPORTED };
  window.t = t; // convenience for app.js

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("lang-toggle");
    if (btn) btn.addEventListener("click", toggle);
    applyI18n();
  });
})();
