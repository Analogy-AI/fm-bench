/* FM Bench web shell — vanilla JS client over the same tool API agents use.
   Information parity is real: every read consumes the same query budget an
   agent gets, so tab data is cached per stop and refreshed only on demand. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let STATE = null;            // last /api/state payload
let CACHE = {};              // per-stop tab data cache, keyed by tool+args
let PREV = null;             // digest snapshot from the previous stop (trends)
let FIN_HIST = [];           // client-side {stop, year, cash, wage_ratio, pos} history
let LAST_YEAR = null;
let ACTIVE_TAB = "squad";
let MY_CID = null;           // resolved lazily from the league table

/* ---------------------------------------------------------------- api */

async function api(path, body) {
  const res = await fetch(path, body === undefined
    ? undefined
    : { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
  return res.json();
}

async function callTool(name, args = {}) {
  const env = await api("/api/tool", { name, args });
  if (!env.ok && env.error) banner(`${env.error.code}: ${env.error.hint}`);
  syncBudgets();
  return env;
}

async function syncBudgets() {
  const st = await api("/api/state");
  if (st && st.budgets && STATE) { STATE.budgets = st.budgets; renderBudgets(); }
}

/* Query with per-stop cache (budget parity: refetch only on demand). */
async function q(name, args = {}, refresh = false) {
  const key = name + JSON.stringify(args);
  if (!refresh && CACHE[key]) return CACHE[key];
  const env = await callTool(name, args);
  if (env.ok) CACHE[key] = env.data;
  return env.ok ? env.data : null;
}

/* ------------------------------------------------------- notifications */

function banner(msg) {
  const b = el("div", "banner");
  b.append(el("span", "", msg));
  const x = el("button", "", t("banner.dismiss"));
  x.onclick = () => b.remove();
  b.append(x);
  $("#banner-area").append(b);
  setTimeout(() => b.remove(), 9000);
}

function toast(msg, mood) {
  const t = el("div", "toast" + (mood ? " " + mood : ""), msg);
  $("#toast-area").append(t);
  setTimeout(() => t.remove(), 6000);
}

/* ------------------------------------------------------------- header */

function fmtMoney(m) {
  return (m >= 0 ? "€" : "-€") + Math.abs(m).toFixed(1) + "M";
}

function trendArrow(delta, goodWhenUp = true) {
  if (!delta) return null;
  const up = delta > 0;
  const cls = "trend " + ((up === goodWhenUp) ? "up" : "down");
  return el("span", cls, up ? "▲" : "▼");
}

function renderBudgets() {
  const b = STATE.budgets;
  const holder = $("#hdr-budget");
  if (!holder || !b) return;
  holder.innerHTML = "";
  const mk = (left, total) => {
    const p = el("span", "pill", `${left}/${total}`);
    if (left === 0) p.classList.add("out");
    else if (left <= Math.ceil(total * 0.2)) p.classList.add("low");
    return p;
  };
  holder.append(mk(b.query_budget - b.queries_used, b.query_budget),
                mk(b.offer_budget - b.offers_used, b.offer_budget));
}

function renderHeader() {
  const h = STATE.header, p = STATE.packet, d = p ? p.digest : null;
  if (!h || !d) return;
  $("#hdr-club").innerHTML = "";
  $("#hdr-club").append(
    el("div", "name", h.name),
    el("div", "sub", t("header.sub", { division: h.division, target: h.season_target, year: d.year_of_run })));
  $("#hdr-date").textContent = `Y${p.date.year} M${p.date.month} D${p.date.day}`;

  const pos = $("#hdr-pos");
  pos.textContent = d.league_position ? `#${d.league_position}` : "—";
  if (PREV && d.league_position && PREV.league_position) {
    const a = trendArrow(PREV.league_position - d.league_position); // lower = better
    if (a) pos.append(a);
  }

  const cash = $("#hdr-cash");
  cash.textContent = fmtMoney(d.cash_m);
  if (PREV) {
    const a = trendArrow(+(d.cash_m - PREV.cash_m).toFixed(1));
    if (a) cash.append(a);
  }

  const conf = d.board_confidence;
  $("#hdr-conf").textContent = conf;
  const fill = $("#hdr-conf-fill");
  fill.style.width = Math.max(2, Math.min(100, conf)) + "%";
  fill.style.background = conf < 30 ? "var(--bad)" : conf < 50 ? "var(--warn)" : "var(--good)";

  renderBudgets();
}

/* -------------------------------------------------- since-last-stop card */

function renderSince() {
  const holder = $("#since-card");
  holder.innerHTML = "";
  const d = STATE.packet ? STATE.packet.digest : null;
  if (!d || !PREV) return;
  const row = el("div", "row");
  const item = (label, delta, fmt, goodWhenUp = true) => {
    if (!delta) return;
    const s = el("span", "", label + " ");
    const b = el("b", (delta > 0) === goodWhenUp ? "up" : "down",
      (delta > 0 ? "+" : "−") + fmt(Math.abs(delta)));
    s.append(b);
    row.append(s);
  };
  item(t("since.points"), d.points - PREV.points, String);
  if (d.league_position && PREV.league_position)
    item(t("since.places"), PREV.league_position - d.league_position, String);
  item(t("since.cash"), +(d.cash_m - PREV.cash_m).toFixed(1), (x) => x.toFixed(1) + "M");
  item(t("since.board"), d.board_confidence - PREV.board_confidence, String);
  if (!row.childNodes.length) return;
  const card = el("div", "since");
  card.append(el("div", "faint", t("since.title")), row);
  holder.append(card);
}

/* -------------------------------------------------------------- inbox */

// values are i18n keys, resolved via t() at render time so a language toggle updates them
const NICE_TYPE = {
  transfer_offer: "evt.transferOffer", transfer_completed: "evt.transferCompleted",
  offer_expired: "evt.offerExpired", offer_auto_rejected: "evt.offerAutoRejected",
  board_warning: "evt.boardWarning", major_injury: "evt.majorInjury",
  contract_expiring: "evt.contractExpiring", youth_intake: "evt.youthIntake",
  facility_complete: "evt.facilityComplete", player_left_free: "evt.playerLeftFree",
  player_retired: "evt.playerRetired", administration: "evt.administration",
};
const SEVERITY = {
  transfer_offer: "sev-gold", board_warning: "sev-bad", major_injury: "sev-bad",
  administration: "sev-bad", contract_expiring: "sev-warn", offer_expired: "sev-warn",
  youth_intake: "sev-good", facility_complete: "sev-good",
  transfer_completed: "sev-good",
};

function payloadText(pl) {
  return Object.entries(pl).map(([k, v]) => {
    if (k === "amount" && typeof v === "number" && v > 100000)
      v = fmtMoney(v / 1e8); // cents -> €M
    return `${k}: ${v}`;
  }).join(" · ");
}

function offerControls(offer) {
  const c = el("div", "controls");
  const done = (env, msg) => {
    if (env.ok) { toast(msg, "good"); refreshInboxAfterAction(); }
  };
  if (offer.direction === "incoming") {
    const acc = el("button", "primary", t("offer.accept"));
    acc.onclick = async () =>
      done(await callTool("respond_to_offer", { offer_id: offer.oid, action: "accept" }), t("toast.sold"));
    const rej = el("button", "", t("offer.reject"));
    rej.onclick = async () =>
      done(await callTool("respond_to_offer", { offer_id: offer.oid, action: "reject" }), t("toast.rejected"));
    const amt = el("input"); amt.type = "number"; amt.placeholder = "€M";
    const ctr = el("button", "", t("offer.counter"));
    ctr.onclick = async () => {
      if (!amt.value) return;
      done(await callTool("respond_to_offer",
        { offer_id: offer.oid, action: "counter", counter_amount_m: +amt.value }), t("toast.countered"));
    };
    c.append(acc, rej, amt, ctr);
  } else { // outgoing
    if (offer.status === "countered") {
      const acc = el("button", "primary", t("offer.acceptCounter", { amount: fmtMoney(offer.counter_amount_m) }));
      acc.onclick = async () =>
        done(await callTool("respond_to_offer", { offer_id: offer.oid, action: "accept_counter" }), t("toast.bought"));
      c.append(acc);
      const amt = el("input"); amt.type = "number"; amt.placeholder = "€M";
      const ctr = el("button", "", t("offer.recounter"));
      ctr.onclick = async () => {
        if (!amt.value) return;
        done(await callTool("respond_to_offer",
          { offer_id: offer.oid, action: "counter", counter_amount_m: +amt.value }), t("toast.countered"));
      };
      c.append(amt, ctr);
    }
    const wd = el("button", "danger", t("offer.withdraw"));
    wd.onclick = async () =>
      done(await callTool("respond_to_offer", { offer_id: offer.oid, action: "withdraw" }), t("toast.withdrawn"));
    c.append(wd);
  }
  return c;
}

async function refreshInboxAfterAction() {
  const data = await q("get_inbox", {}, true);
  if (data) renderPendingOffers(data.pending_offers);
}

function renderPendingOffers(offers) {
  const holder = $("#pending-offers");
  if (!holder) return;
  holder.innerHTML = "";
  if (!offers.length) {
    holder.append(el("div", "empty", t("offer.empty")));
    return;
  }
  for (const o of offers) {
    const item = el("div", "inbox-item sev-gold");
    const t = el("div", "t");
    t.append(el("span", "tag", o.direction === "incoming" ? window.t("offer.selling") : window.t("offer.buying")),
      document.createTextNode(`${o.player} — ${fmtMoney(o.amount_m)}`));
    item.append(t);
    item.append(el("div", "d",
      `${o.direction === "incoming" ? window.t("offer.from") : window.t("offer.to")} ${o.buyer} · ${o.status}` +
      ` · ${window.t("offer.expiresIn", { days: o.expires_in_days })}`));
    if (o.counter_amount_m)
      item.append(el("div", "offer-history",
        window.t("offer.theirCounter", { amount: fmtMoney(o.counter_amount_m) })));
    item.append(offerControls(o));
    holder.append(item);
  }
}

function renderInbox() {
  const p = STATE.packet;
  const list = $("#inbox-list");
  list.innerHTML = "";
  $("#inbox-title").textContent =
    t("inbox.titleFull", { stop: p.stop_id, types: p.stop_types.join(", ") });

  renderSince();

  const w = $("#stop-warnings");
  w.innerHTML = "";
  for (const warn of p.digest.warnings)
    w.append(el("span", "chip bad", warn.replaceAll("_", " ")));
  if (p.digest.window !== "closed")
    w.append(el("span", "chip gold", t("inbox.windowOpen", { window: p.digest.window })));
  if (p.digest.injured_players)
    w.append(el("span", "chip warn", t("inbox.injured", { n: p.digest.injured_players })));
  if (p.digest.next_fixture) {
    const nf = p.digest.next_fixture;
    w.append(el("span", "chip",
      t("inbox.next", { loc: nf.home ? "vs" : "@", opponent: nf.opponent, day: nf.day })));
  }

  const offersHolder = el("div"); offersHolder.id = "pending-offers";
  list.append(el("h2", "", t("inbox.negotiations")), offersHolder);
  renderPendingOffers(p.pending_offers);

  list.append(el("h2", "", t("inbox.events")));
  const items = [...p.inbox].sort((a, b) => (b.action_required - a.action_required));
  if (!items.length)
    list.append(el("div", "empty", t("inbox.quiet")));
  for (const it of items) {
    if (it.type === "transfer_offer") continue; // rendered as negotiation above
    const item = el("div", "inbox-item " + (SEVERITY[it.type] || ""));
    const t = el("div", "t");
    t.append(el("span", "tag", it.action_required ? window.t("tag.action") : window.t("tag.info")),
      document.createTextNode(NICE_TYPE[it.type] ? window.t(NICE_TYPE[it.type]) : it.type));
    item.append(t);
    item.append(el("div", "d", `Y${it.year} D${it.day} · ${payloadText(it.payload)}`));
    list.append(item);
  }
}

/* --------------------------------------------------------------- tabs */

function sortable(table, rows, cols, renderRow, rowClassTest) {
  const thead = el("thead"); const tr = el("tr");
  let sortKey = null, asc = true;
  const ths = [];
  const tbody = el("tbody");
  const draw = () => {
    tbody.innerHTML = "";
    const sorted = [...rows];
    if (sortKey !== null) sorted.sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      const c = (typeof x === "number" && typeof y === "number")
        ? x - y : String(x).localeCompare(String(y));
      return asc ? c : -c;
    });
    for (const r of sorted) {
      const row = renderRow(r);
      if (rowClassTest) {
        const cls = rowClassTest(r);
        if (cls) row.classList.add(...cls.split(" ").filter(Boolean));
      }
      tbody.append(row);
    }
  };
  for (const [label, key] of cols) {
    const th = el("th", "", label);
    if (key) th.onclick = () => {
      asc = sortKey === key ? !asc : true; sortKey = key;
      ths.forEach(x => x.classList.remove("sorted"));
      th.classList.add("sorted");
      draw();
    };
    ths.push(th);
    tr.append(th);
  }
  thead.append(tr); table.append(thead, tbody);
  draw();
}

/* Ability is a band, never a point — render as a range bar.
   Display CA runs on a 0–200 scale; 30–170 covers the realistic range. */
function abilityCell(a) {
  const holder = el("div", "band conf-" + a.confidence);
  holder.append(el("span", "nums", `${a.ca_low}–${a.ca_high}`));
  const track = el("div", "track");
  const range = el("div", "range");
  const lo = Math.max(0, (a.ca_low - 30) / 140 * 100);
  const hi = Math.min(100, (a.ca_high - 30) / 140 * 100);
  range.style.left = lo + "%";
  range.style.width = Math.max(3, hi - lo) + "%";
  track.append(range);
  holder.append(track, el("span", "conf", a.confidence));
  return holder;
}

function ageCell(age) {
  const td = el("td", "mono", String(age));
  if (age <= 21) td.classList.add("age-young");
  else if (age >= 30) td.classList.add("age-old");
  return td;
}

function dealCell(months) {
  const td = el("td", "mono", months + "m");
  if (months <= 6) td.classList.add("deal-short");
  else if (months <= 12) td.classList.add("deal-mid");
  return td;
}

function formCell(f) {
  return el("td", f > 0.5 ? "form-pos" : f < -0.5 ? "form-neg" : "mono",
    (f > 0 ? "+" : "") + f);
}

function nameCell(p) {
  const td = el("td");
  td.append(document.createTextNode(p.name + " "));
  if (p.listed) td.append(el("span", "badge-listed", t("badge.listed")), document.createTextNode(" "));
  if (p.injury_days) td.append(el("span", "badge-inj", `✚${p.injury_days}d`));
  return td;
}

function playerActions(p, opts = {}) {
  const c = el("div", "rowbtns");
  const detail = el("button", "", t("action.view"));
  detail.onclick = () => showPlayerDetail(p.pid);
  c.append(detail);
  if (opts.own) {
    const list = el("button", "", p.listed ? t("action.unlist") : t("action.list"));
    list.onclick = async () => {
      const env = await callTool("list_player", { player_id: p.pid, listed: !p.listed });
      if (env.ok) { toast(env.data.listed ? t("toast.listed") : t("toast.unlisted"), "good"); reloadTab(true); }
    };
    const rel = el("button", "danger", t("action.release"));
    rel.onclick = async () => {
      if (!confirm(t("confirm.release", { name: p.name }))) return;
      const env = await callTool("release_player", { player_id: p.pid });
      if (env.ok) { toast(t("toast.released", { amount: fmtMoney(env.data.severance_m) }), "good"); reloadTab(true); }
    };
    c.append(list, rel);
  }
  if (opts.youth) {
    const pr = el("button", "primary", t("action.promote"));
    pr.onclick = async () => {
      const env = await callTool("promote_youth", { player_id: p.pid });
      if (env.ok) { toast(t("toast.promoted"), "good"); reloadTab(true); }
    };
    c.append(pr);
  }
  if (opts.market) {
    if (p.status === "free_agent") {
      const wage = el("input"); wage.type = "number"; wage.placeholder = t("ph.wage");
      const sign = el("button", "primary", t("action.sign"));
      sign.onclick = async () => {
        if (!wage.value) return;
        const env = await callTool("offer_contract",
          { player_id: p.pid, wage_m_per_year: +wage.value, years: 3 });
        if (env.ok) {
          toast(env.data.accepted ? t("toast.signed") :
            (env.data.counter_wage_m ? t("toast.wantsMoreWage", { amount: fmtMoney(env.data.counter_wage_m) }) : t("toast.wantsMore")),
            env.data.accepted ? "good" : undefined);
          reloadTab(true);
        }
      };
      c.append(wage, sign);
    } else {
      const amt = el("input"); amt.type = "number"; amt.placeholder = t("ph.fee");
      const bid = el("button", "primary", t("action.bid"));
      bid.onclick = async () => {
        if (!amt.value) return;
        const env = await callTool("make_transfer_offer",
          { player_id: p.pid, amount_m: +amt.value });
        if (env.ok) {
          toast(t("toast.offer", { status: env.data.status }) +
            (env.data.counter_amount_m ? t("toast.offerCounter", { amount: fmtMoney(env.data.counter_amount_m) }) : ""));
          refreshInboxAfterAction();
        }
      };
      c.append(amt, bid);
    }
  }
  return c;
}

async function showPlayerDetail(pid) {
  const data = await q("get_player", { player_id: pid });
  if (!data) return;
  const body = $("#tab-body");
  const card = el("div", "detail-card");
  const head = el("div", "head");
  head.append(el("span", "n", `${data.name} — ${data.position}, ${data.age}`));
  const close = el("button", "ghost", t("action.close"));
  close.onclick = () => card.remove();
  head.append(close);
  card.append(head);

  const kv = el("div", "kv");
  const put = (k, v, mono) => {
    kv.append(el("div", "", k));
    const d = el("div", mono ? "m" : "");
    if (v instanceof Node) d.append(v); else d.textContent = String(v);
    kv.append(d);
  };
  put(t("detail.club"), data.club || t("detail.freeAgent"));
  put(t("detail.ability"), abilityCell(data.ability));
  put(t("detail.form"), (data.form > 0 ? "+" : "") + data.form, true);
  put(t("detail.contract"), data.contract_months_left ? t("detail.monthsLeft", { months: data.contract_months_left }) : "—", true);
  put(t("detail.thisSeason"), t("detail.seasonStats", { apps: data.season_stats.apps, goals: data.season_stats.goals, assists: data.season_stats.assists, minutes: data.season_stats.minutes }), true);
  put(t("detail.fitness"), data.injury_days ? t("detail.injured", { days: data.injury_days }) : t("detail.fit"));
  if (data.wage_m_per_year !== undefined) put(t("detail.wage"), fmtMoney(data.wage_m_per_year) + "/y", true);
  if (data.morale) put(t("detail.morale"), `${data.morale} / ${data.fatigue}`);
  if (data.potential_stars) put(t("detail.potential"), "★".repeat(data.potential_stars));
  card.append(kv);

  if (data.wage_m_per_year !== undefined) { // own player -> renewal controls
    const fr = el("div", "formrow");
    const wage = el("input"); wage.type = "number"; wage.placeholder = t("ph.wage");
    const yrs = el("select");
    for (const y of [1, 2, 3, 4, 5]) { const o = el("option", "", y + "y"); o.value = y; yrs.append(o); }
    yrs.value = 3;
    const renew = el("button", "primary", t("action.offerContract"));
    renew.onclick = async () => {
      if (!wage.value) return;
      const env = await callTool("offer_contract",
        { player_id: pid, wage_m_per_year: +wage.value, years: +yrs.value });
      if (env.ok) toast(env.data.accepted ? t("toast.contractAgreed") :
        (env.data.counter_wage_m ? t("toast.contractWantsWage", { amount: fmtMoney(env.data.counter_wage_m) }) : t("toast.contractRejected")),
        env.data.accepted ? "good" : undefined);
    };
    fr.append(el("span", "muted", t("detail.renew")), wage, yrs, renew);
    card.append(fr);
  }
  body.prepend(card);
}

/* Resolve my club id from the (cached) league table, for match reports. */
async function myCid() {
  if (MY_CID) return MY_CID;
  const table = await q("get_league_table", {});
  if (!table) return null;
  const mine = table.find(r => r.club === (STATE.header && STATE.header.name));
  MY_CID = mine ? mine.club_id : null;
  return MY_CID;
}

async function showMatchReport(fix) {
  const cid = await myCid();
  if (!cid) { toast(t("toast.noCid")); return; }
  const year = STATE.packet.date.year;
  const home = fix.home ? cid : fix.opponent_id;
  const mid = `M${String(year).padStart(2, "0")}R${String(fix.round - 1).padStart(2, "0")}${home}`;
  const data = await q("get_match_report", { match_id: mid });
  if (!data) return;
  const body = $("#tab-body");
  const card = el("div", "detail-card");
  const head = el("div", "head");
  head.append(el("span", "n",
    t("report.title", { home: data.home, hg: data.home_goals, ag: data.away_goals, away: data.away, round: data.round })));
  const close = el("button", "ghost", t("action.close"));
  close.onclick = () => card.remove();
  head.append(close);
  card.append(head);
  if (data.goals.length)
    card.append(el("div", "offer-history",
      data.goals.map(x => `${x.minute}' ${x.scorer} (${x.side})`).join(" · ")));
  const rows = Object.values(data.players);
  const table = el("table");
  sortable(table, rows, [[t("col.player"), "name"], [t("col.rating"), "rating"], [t("col.goals"), "goals"], [t("col.assists"), "assists"]],
    (r) => {
      const tr = el("tr");
      tr.append(el("td", "", r.name), el("td", "mono", String(r.rating)),
        el("td", "mono", String(r.goals)), el("td", "mono", String(r.assists)));
      return tr;
    });
  card.append(table);
  body.prepend(card);
}

const TABS = {
  async squad(body, refresh) {
    const data = await q("get_squad", {}, refresh);
    if (!data) return;
    const picks = new Set();

    const top = el("div", "tabtop");
    const setXI = el("button", "primary", t("squad.setLineup", { n: 0 }));
    setXI.onclick = async () => {
      const env = await callTool("set_lineup", { player_ids: [...picks] });
      if (env.ok) toast(t("toast.lineupSet"), "good");
    };
    top.append(setXI,
      el("span", "faint", t("squad.hint")));
    body.append(top);

    const table = el("table");
    sortable(table, data, [["✓"], [t("col.player"), "name"], [t("col.pos"), "position"], [t("col.age"), "age"],
      [t("col.ability")], [t("col.form"), "form"], [t("col.wage"), "wage_m_per_year"],
      [t("col.deal"), "contract_months_left"], [t("col.season")], [t("col.actions")]],
      (p) => {
        const tr = el("tr");
        const cb = el("input"); cb.type = "checkbox";
        cb.checked = picks.has(p.pid);
        cb.onchange = () => {
          cb.checked ? picks.add(p.pid) : picks.delete(p.pid);
          setXI.textContent = t("squad.setLineup", { n: picks.size });
        };
        const cbtd = el("td"); cbtd.append(cb);
        const ab = el("td"); ab.append(abilityCell(p.ability));
        const act = el("td"); act.append(playerActions(p, { own: true }));
        tr.append(cbtd, nameCell(p), el("td", "", p.position), ageCell(p.age),
          ab, formCell(p.form),
          el("td", "mono", fmtMoney(p.wage_m_per_year) + "/y"),
          dealCell(p.contract_months_left),
          el("td", "mono", `${p.season_stats.apps}a ${p.season_stats.goals}g`),
          act);
        return tr;
      });
    body.append(table);
  },

  async tactics(body) {
    const ov = await q("get_club_overview");
    if (!ov) return;
    const fr = el("div", "formrow");
    const formation = el("select");
    for (const f of ["3-5-2", "4-3-3", "4-4-2"]) {
      const o = el("option", "", f); o.value = f; formation.append(o);
    }
    formation.value = ov.formation;
    const style = el("select");
    for (const s of ["gegenpress", "lowblock", "possession"]) {
      const o = el("option", "", t("tactics.style." + s)); o.value = s; style.append(o);
    }
    style.value = ov.style;
    const apply = el("button", "primary", t("action.apply"));
    apply.onclick = async () => {
      const env = await callTool("set_tactics",
        { formation: formation.value, style: style.value });
      if (env.ok) { toast(t("toast.tactics", { formation: env.data.formation, style: t("tactics.style." + env.data.style) }), "good"); reloadTab(true); }
    };
    fr.append(el("span", "muted", t("tactics.formation")), formation,
      el("span", "muted", t("tactics.styleLabel")), style, apply);
    body.append(fr, el("p", "muted", t("tactics.note")));
    const kv = el("div", "kv");
    kv.append(el("div", "", t("tactics.currentSetup")), el("div", "m", `${ov.formation} · ${t("tactics.style." + ov.style)}`),
      el("div", "", t("club.reputation")), el("div", "m", String(ov.reputation)),
      el("div", "", t("tactics.stadium")), el("div", "m", t("tactics.seats", { n: ov.stadium_capacity.toLocaleString() })));
    body.append(kv);
  },

  async table(body, refresh) {
    const data = await q("get_league_table", {}, refresh);
    if (!data) return;
    const myName = STATE.header ? STATE.header.name : "";
    const div = STATE.packet.digest.division;
    const n = data.length;
    const table = el("table");
    sortable(table, data, [[t("col.rank"), "position"], [t("col.club"), "club"], [t("col.p"), "played"],
      [t("col.w"), "won"], [t("col.d"), "drawn"], [t("col.l"), "lost"], [t("col.gd"), "goal_diff"], [t("col.pts"), "points"]],
      (r) => {
        const tr = el("tr");
        tr.append(el("td", "mono", String(r.position)), el("td", "", r.club),
          el("td", "mono", String(r.played)), el("td", "mono", String(r.won)),
          el("td", "mono", String(r.drawn)), el("td", "mono", String(r.lost)),
          el("td", "mono", String(r.goal_diff)), el("td", "mono", String(r.points)));
        return tr;
      }, (r) => {
        const cls = [];
        if (r.club === myName) cls.push("me");
        if (r.position === 1) cls.push("zone-title");
        else if (div === 2 && r.position <= 3) cls.push("zone-up");
        if (r.position > n - 3) cls.push("zone-down");
        return cls.join(" ");
      });
    body.append(table);
    body.append(el("p", "faint", div === 1 ? t("table.legend1") : t("table.legend2")));
  },

  async fixtures(body, refresh) {
    const data = await q("get_fixtures", {}, refresh);
    if (!data) return;

    const played = data.filter(f => f.result);
    if (played.length) {
      const dots = el("span", "formdots");
      for (const f of played.slice(-5)) {
        const [gf, ga] = f.result.split("-").map(Number);
        const mine = f.home ? [gf, ga] : [ga, gf];
        dots.append(el("i", mine[0] > mine[1] ? "W" : mine[0] === mine[1] ? "D" : "L"));
      }
      const top = el("div", "tabtop");
      top.append(el("span", "muted", t("fixtures.form")), dots);
      body.append(top);
    }

    const table = el("table");
    sortable(table, data, [[t("col.rd"), "round"], [t("col.day"), "day"], [t("col.ha")], [t("col.opponent"), "opponent"], [t("col.result")], [""]],
      (f) => {
        const tr = el("tr");
        const res = el("td", "mono", f.result || "—");
        if (f.result) {
          const [gf, ga] = f.result.split("-").map(Number);
          const mine = f.home ? [gf, ga] : [ga, gf];
          res.className = "mono " + (mine[0] > mine[1] ? "form-pos" : mine[0] < mine[1] ? "form-neg" : "");
        }
        const act = el("td");
        if (f.result) {
          const b = el("button", "", t("action.report"));
          b.onclick = () => showMatchReport(f);
          const btns = el("div", "rowbtns"); btns.append(b); act.append(btns);
        }
        tr.append(el("td", "mono", String(f.round)), el("td", "mono", String(f.day)),
          el("td", "", f.home ? t("fixtures.h") : t("fixtures.a")), el("td", "", f.opponent), res, act);
        return tr;
      });
    body.append(table);
  },

  async market(body, refresh) {
    const data = await q("get_transfer_market", {}, refresh);
    if (!data) return;
    body.append(el("p", "muted", t("market.note")));
    if (!data.length) { body.append(el("div", "empty", t("market.empty"))); return; }
    const table = el("table");
    const KNOWN_STATUS = { free_agent: 1, listed: 1, contract_expiring: 1, transfer_listed: 1, loan_listed: 1 };
    const statusLabel = (s) => KNOWN_STATUS[s] ? t("market.status." + s) : s.replaceAll("_", " ");
    sortable(table, data, [[t("col.player"), "name"], [t("col.pos"), "position"], [t("col.age"), "age"],
      [t("col.ability")], [t("col.club"), "club"], [t("col.status"), "status"], [t("col.deal"), "contract_months_left"], [t("col.actions")]],
      (p) => {
        const tr = el("tr");
        const ab = el("td"); ab.append(abilityCell(p.ability));
        const act = el("td"); act.append(playerActions(p, { market: true }));
        tr.append(el("td", "", p.name), el("td", "", p.position), ageCell(p.age),
          ab, el("td", "", p.club || "—"),
          el("td", "", statusLabel(p.status)),
          dealCell(p.contract_months_left), act);
        return tr;
      });
    body.append(table);
  },

  async finances(body, refresh) {
    const data = await q("get_finances", {}, refresh);
    if (!data) return;

    const kv = el("div", "kv");
    const put = (k, v) => { kv.append(el("div", "", k)); kv.append(el("div", "m", String(v))); };
    put(t("fin.cash"), fmtMoney(data.cash_m));
    put(t("fin.revLast"), fmtMoney(data.revenue_last_season_m));
    put(t("fin.revYtd"), fmtMoney(data.revenue_ytd_m));
    put(t("fin.costYtd"), fmtMoney(data.cost_ytd_m));
    put(t("fin.wageBill"), fmtMoney(data.wage_bill_m));
    if (data.parachute_years_left) put(t("fin.parachute"), data.parachute_years_left);
    if (data.in_administration) put(t("fin.inAdmin"), t("fin.yes"));
    body.append(kv);

    const viz = el("div", "finviz");

    // wage-ratio gauge with the healthy band marked
    const wr = data.wage_ratio;
    const g = el("div", "hbar");
    const lbl = el("div", "lbl");
    lbl.append(el("span", "", t("fin.wageRatio")),
      el("b", "", Math.round(wr * 100) + "%"));
    const track = el("div", "track");
    const band = el("div", "band-ok");
    band.style.left = "55%"; band.style.width = "15%";
    const fill = el("div", "fill");
    fill.style.width = Math.min(100, wr * 100) + "%";
    fill.style.background = wr > 0.85 ? "var(--bad)" : wr > 0.70 ? "var(--warn)" : "var(--good)";
    const mark = el("div", "mark"); mark.style.left = "85%"; mark.title = t("fin.warnLine");
    track.append(band, fill, mark);
    g.append(lbl, track);
    viz.append(g);

    // revenue vs wages bar
    const rv = Math.max(data.revenue_last_season_m, 1);
    const h2 = el("div", "hbar");
    const lbl2 = el("div", "lbl");
    lbl2.append(el("span", "", t("fin.wagesVsRev")),
      el("b", "", `${fmtMoney(data.wage_bill_m)} / ${fmtMoney(data.revenue_last_season_m)}`));
    const track2 = el("div", "track");
    const fill2 = el("div", "fill");
    fill2.style.width = Math.min(100, data.wage_bill_m / rv * 100) + "%";
    fill2.style.background = "var(--gold-dim)";
    track2.append(fill2);
    h2.append(lbl2, track2);
    viz.append(h2);

    // client-side cash sparkline across stops
    if (FIN_HIST.length >= 2) {
      const sp = el("div", "spark");
      sp.append(el("div", "cap", t("fin.cashAcross", { n: FIN_HIST.length })));
      const cv = el("canvas", "sparkline");
      sp.append(cv);
      viz.append(sp);
      requestAnimationFrame(() => drawSparkline(cv, FIN_HIST.map(x => x.cash)));
    }
    body.append(viz);
    body.append(el("p", "muted", t("fin.note")));
  },

  async club(body, refresh) {
    const ov = await q("get_club_overview", {}, refresh);
    if (!ov) return;
    const kv = el("div", "kv");
    const put = (k, v) => { kv.append(el("div", "", k)); kv.append(el("div", "m", String(v))); };
    put(t("club.division"), ov.division);
    put(t("club.position"), ov.league_position ?? "—");
    put(t("club.boardConf"), ov.board_confidence);
    put(t("club.seasonTarget"), t("club.top", { n: ov.season_target }));
    put(t("club.reputation"), ov.reputation);
    put(t("club.stadiumCap"), ov.stadium_capacity.toLocaleString());
    put(t("club.academy"), ov.academy_level + "/3");
    put(t("club.training"), ov.training_level + "/3");
    const kindLabel = (k) => ({ academy: 1, training: 1, stadium: 1 }[k] ? t("club.kind." + k) : k);
    put(t("club.projects"), ov.facility_projects.map(p => t("club.project", { kind: kindLabel(p.kind), days: p.days_left })).join(", ") || t("club.none"));
    body.append(kv);
    const fr = el("div", "formrow");
    for (const kind of ["academy", "training", "stadium"]) {
      const b = el("button", "", t("club.invest", { kind: kindLabel(kind) }));
      b.onclick = async () => {
        const env = await callTool("invest", { kind });
        if (env.ok) { toast(t("toast.investStarted", { kind: kindLabel(kind), cost: fmtMoney(env.data.cost_m), days: env.data.days }), "good"); reloadTab(true); }
      };
      fr.append(b);
    }
    body.append(el("p", "muted", t("club.note")), fr);
  },

  async youth(body, refresh) {
    const data = await q("get_youth_academy", {}, refresh);
    if (!data) return;
    if (!data.length) { body.append(el("div", "empty", t("youth.empty"))); return; }
    const table = el("table");
    sortable(table, data, [[t("col.player"), "name"], [t("col.pos"), "position"], [t("col.age"), "age"],
      [t("col.ability")], [t("col.potential")], [t("col.actions")]],
      (p) => {
        const tr = el("tr");
        const ab = el("td"); ab.append(abilityCell(p.ability));
        const act = el("td"); act.append(playerActions(p, { youth: true }));
        tr.append(el("td", "", p.name), el("td", "", p.position), ageCell(p.age),
          ab, el("td", "", "★".repeat(p.potential_stars || 0)), act);
        return tr;
      });
    body.append(table);
    body.append(el("p", "muted", t("youth.note")));
  },

  async history(body, refresh) {
    for (const topic of ["honors", "seasons", "transfers"]) {
      const data = await q("get_history", { topic }, refresh);
      if (!data) continue;
      body.append(el("h2", "", t("history." + topic)));
      const rows = data[topic] || [];
      if (!rows.length) { body.append(el("div", "empty", t("history.empty"))); continue; }
      const table = el("table");
      const cols = Object.keys(rows[0]).map(k => [k.replaceAll("_", " "), k]);
      sortable(table, rows, cols, (r) => {
        const tr = el("tr");
        for (const k of Object.keys(rows[0])) tr.append(el("td", "", String(r[k])));
        return tr;
      });
      body.append(table);
    }
  },

  async notebook(body) {
    const ta = el("textarea"); ta.id = "nb";
    ta.value = STATE.packet.notebook || "";
    const count = el("span", ""); count.id = "nb-count";
    const upd = () => { count.textContent = t("nb.chars", { n: ta.value.length }); };
    ta.oninput = upd; upd();
    const save = el("button", "primary", t("nb.save"));
    save.onclick = async () => {
      const env = await callTool("rewrite_notes", { text: ta.value });
      if (env.ok) {
        toast(t("toast.notebookSaved", { used: env.data.chars_used, cap: env.data.chars_cap }), "good");
        STATE.packet.notebook = ta.value;
        count.textContent = t("nb.charsCap", { used: env.data.chars_used, cap: env.data.chars_cap });
      }
    };
    body.append(el("p", "muted", t("nb.note")));
    body.append(ta);
    const fr = el("div", "formrow");
    fr.append(save, count);
    body.append(fr);
  },
};

async function reloadTab(refresh = false) {
  const body = $("#tab-body");
  body.innerHTML = "";
  const bar = el("div", "tabtop");
  const rf = el("button", "ghost", t("action.refresh"));
  rf.onclick = () => reloadTab(true);
  bar.append(el("span", "spacer"), rf);
  body.append(bar);
  await TABS[ACTIVE_TAB](body, refresh);
}

/* ---------------------------------------------------- season rollover */

function showSeasonReview(prevDigest, newYear) {
  const body = $("#so-body");
  body.innerHTML = "";
  $("#so-year").textContent = `Y${newYear - 1} → Y${newYear}`;
  const kv = el("div", "kv");
  const put = (k, v) => { kv.append(el("div", "", k)); kv.append(el("div", "m", String(v))); };
  if (prevDigest) {
    put(t("review.finished"), prevDigest.league_position
      ? t("review.finishedVal", { pos: prevDigest.league_position, points: prevDigest.points }) : "—");
    put(t("review.targetWas"), t("club.top", { n: prevDigest.season_target }));
    put(t("review.cashEnd"), fmtMoney(prevDigest.cash_m));
    put(t("review.boardConf"), prevDigest.board_confidence);
  }
  body.append(kv);
  const seasonCash = FIN_HIST.filter(x => x.year === newYear - 1).map(x => x.cash);
  if (seasonCash.length >= 2) {
    const sp = el("div", "spark");
    sp.append(el("div", "cap", t("review.cashThrough")));
    const cv = el("canvas", "sparkline");
    sp.append(cv);
    body.append(sp);
    requestAnimationFrame(() => drawSparkline(cv, seasonCash));
  }
  body.append(el("p", "muted", t("review.note")));
  $("#season-over").classList.remove("hidden");
}

/* ------------------------------------------------------------ overlays */

function ladderRow(holder, who, val, lo, hi, isYou) {
  holder.append(el("span", "who" + (isYou ? " you" : ""), who));
  const bar = el("div", "bar" + (isYou ? " you" : ""));
  const i = el("i");
  // value-added scores span negatives; map [lo, hi] -> [2%, 100%]
  const pct = hi > lo ? (val - lo) / (hi - lo) * 100 : 50;
  i.style.width = Math.max(2, Math.min(100, pct)) + "%";
  bar.append(i);
  holder.append(bar);
  holder.append(el("span", "val" + (isYou ? " you" : ""), val.toFixed(1)));
}

function showGameOver() {
  const r = STATE.result;
  $("#go-title").textContent =
    r.settle_reason === "completed" ? t("gameover.complete") :
    r.settle_reason === "fired" ? t("gameover.sacked") :
    r.settle_reason === "administration" ? t("gameover.admin") :
    t("gameover.over");
  const body = $("#go-body");
  body.innerHTML = "";
  const box = el("div", "scorebox");

  const big = el("div", "score-big");
  big.append(el("span", "cap", t("gameover.composite")));
  big.append(document.createTextNode(String(r.s_final)));
  box.append(big);

  const reasonLabel = { completed: 1, fired: 1, administration: 1 }[r.settle_reason]
    ? t("gameover.reason." + r.settle_reason) : r.settle_reason;
  const settle = el("div", "settle-line");
  settle.append(document.createTextNode(t("gameover.settledPrefix")));
  settle.append(el("b", "", reasonLabel));
  settle.append(document.createTextNode(
    t("gameover.atYear", { t: (r.settle_t && r.settle_t.toFixed) ? r.settle_t.toFixed(1) : r.settle_t, years: STATE.years })));
  settle.append(el("span", "rho", `ρ = ${r.rho}`));
  settle.append(document.createTextNode(
    t("gameover.stops", { stops: r.stops_total, seed: r.seed })));
  box.append(settle);

  // channel bars, animated on reveal
  const channels = el("div", "channels");
  const fills = [];
  const chan = (label, term) => {
    const c = el("div", "channel");
    const lbl = el("div", "lbl");
    lbl.append(el("span", "", label), el("b", "", String(term)));
    const track = el("div", "track");
    const fill = el("div", "fill");
    fill.dataset.w = Math.max(2, Math.min(100, term * 6)); // visual scale
    track.append(fill);
    c.append(lbl, track);
    channels.append(c);
    fills.push(fill);
  };
  chan(t("gameover.chanHonors", { pts: r.channels.honors_points }), r.channels.honors_term);
  chan(t("gameover.chanNetWorth", { val: fmtMoney(r.channels.net_worth_real_m) }), r.channels.net_worth_term);
  chan(t("gameover.chanSquadValue", { val: fmtMoney(r.channels.squad_value_real_m) }), r.channels.squad_value_term);
  box.append(channels);

  // context ladder (5y calibration baselines only — 20y has no calibrated refs yet)
  if (STATE.years === 5) {
    box.append(el("h2", "", t("gameover.whereLand")));
    const lad = el("div", "ladder");
    // v0.3 value-added scoring: 0 ~ "left the club as valuable as you found it".
    const entries = [[t("gameover.ladder.random"), -4.8], [t("gameover.ladder.reckless"), 0.3],
      [t("gameover.ladder.scripted"), 4.7], [t("gameover.ladder.oracle"), 4.5], [t("gameover.ladder.you"), r.s_final]];
    const vals = entries.map(e => e[1]).concat([0]);
    const lo = Math.min(...vals), hi = Math.max(...vals, 1);
    for (const [who, val] of entries)
      ladderRow(lad, who, val, lo, hi, who === t("gameover.ladder.you"));
    box.append(lad);
    box.append(el("div", "ladder-note", t("gameover.ladderNote")));
  }

  if ((r.honors_events || []).length) {
    box.append(el("h2", "", t("gameover.honors")));
    const hl = el("div", "honors-list");
    for (const h of r.honors_events) {
      const row = el("div", "row");
      row.append(el("span", "yr", "Y" + h.year),
        el("span", "", h.code.replaceAll("_", " ")),
        el("span", "pts " + (h.points > 0 ? "pos" : "neg"),
          (h.points > 0 ? "+" : "") + h.points));
      hl.append(row);
    }
    box.append(hl);
  }
  body.append(box);
  $("#game-over").classList.remove("hidden");
  requestAnimationFrame(() =>
    fills.forEach(f => { f.style.width = f.dataset.w + "%"; }));
}

/* ---------------------------------------------------------- sparkline */

function drawSparkline(canvas, series) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 300, h = canvas.clientHeight || 44;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const min = Math.min(...series, 0), max = Math.max(...series, 1);
  const x = (i) => 4 + i / Math.max(1, series.length - 1) * (w - 8);
  const y = (v) => h - 6 - (v - min) / Math.max(1e-9, max - min) * (h - 12);
  if (min < 0) { // zero line when cash has gone negative
    ctx.strokeStyle = "rgba(223,125,125,.5)";
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(0, y(0)); ctx.lineTo(w, y(0)); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.beginPath();
  ctx.moveTo(x(0), y(series[0]));
  series.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(x(series.length - 1), h); ctx.lineTo(x(0), h); ctx.closePath();
  ctx.fillStyle = "rgba(240,185,67,.10)";
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(x(0), y(series[0]));
  series.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.strokeStyle = "rgba(240,185,67,.8)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  const last = series[series.length - 1];
  ctx.beginPath();
  ctx.arc(x(series.length - 1), y(last), 2.5, 0, Math.PI * 2);
  ctx.fillStyle = "#f0b943";
  ctx.fill();
}

/* ---------------------------------------------------------------- flow */

function recordFinancePoint() {
  const d = STATE.packet && STATE.packet.digest;
  if (!d) return;
  FIN_HIST.push({ stop: STATE.packet.stop_id, year: STATE.packet.date.year,
    cash: d.cash_m, wage_ratio: d.wage_ratio, pos: d.league_position });
  if (FIN_HIST.length > 600) FIN_HIST.shift();
}

function renderAll() {
  if (!STATE.started) {
    $("#new-game").classList.remove("hidden");
    $("#topbar").classList.add("hidden");
    $("#layout").classList.add("hidden");
    $("#page-foot").classList.add("hidden");
    return;
  }
  $("#new-game").classList.add("hidden");
  if (STATE.game_over && STATE.result) { showGameOver(); return; }
  $("#topbar").classList.remove("hidden");
  $("#layout").classList.remove("hidden");
  $("#page-foot").classList.remove("hidden");
  renderHeader();
  renderInbox();
  reloadTab();
}

let advancing = false;
async function advance() {
  if (advancing || !STATE || !STATE.started || STATE.game_over) return;
  advancing = true;
  const prevDigest = STATE.packet ? STATE.packet.digest : null;
  document.body.classList.add("simulating");
  $("#btn-continue").disabled = true;
  $("#btn-continue2").disabled = true;
  try {
    const env = await callTool("advance");
    if (!env.ok) return;
    CACHE = {};
    MY_CID = null;
    PREV = prevDigest;
    STATE = await api("/api/state");
    const p = STATE.packet;
    if (p && !p.game_over) {
      recordFinancePoint();
      if (LAST_YEAR !== null && p.date.year !== LAST_YEAR)
        showSeasonReview(prevDigest, p.date.year);
      LAST_YEAR = p.date.year;
    }
    renderAll();
  } finally {
    advancing = false;
    document.body.classList.remove("simulating");
    $("#btn-continue").disabled = false;
    $("#btn-continue2").disabled = false;
  }
}

async function boot() {
  STATE = await api("/api/state");
  if (STATE.started && STATE.packet) {
    LAST_YEAR = STATE.packet.date?.year ?? null;
    recordFinancePoint();
  }
  renderAll();
}

$("#ng-start").onclick = async () => {
  const seed = +$("#ng-seed").value || 42;
  const years = +$("#ng-years").value;
  STATE = await api("/api/new", { seed, years });
  LAST_YEAR = STATE.packet?.date?.year ?? null;
  CACHE = {}; PREV = null; FIN_HIST = []; MY_CID = null;
  recordFinancePoint();
  renderAll();
};
$("#go-new").onclick = () => {
  $("#game-over").classList.add("hidden");
  $("#new-game").classList.remove("hidden");
};
$("#so-close").onclick = () => $("#season-over").classList.add("hidden");
$("#btn-continue").onclick = advance;
$("#btn-continue2").onclick = advance;

const TAB_KEYS = ["squad", "tactics", "table", "fixtures", "market",
  "finances", "club", "youth", "history", "notebook"];
function activateTab(name) {
  ACTIVE_TAB = name;
  document.querySelectorAll("#tabs button").forEach(x =>
    x.classList.toggle("active", x.dataset.tab === name));
  reloadTab();
}
for (const b of document.querySelectorAll("#tabs button"))
  b.onclick = () => activateTab(b.dataset.tab);

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (!STATE || !STATE.started || STATE.game_over) return;
  const overlayOpen = !$("#season-over").classList.contains("hidden") ||
                      !$("#game-over").classList.contains("hidden");
  if (e.key === "c" && !overlayOpen) { e.preventDefault(); advance(); }
  if (e.key === "Escape") $("#season-over").classList.add("hidden");
  if (overlayOpen) return;
  const idx = e.key === "0" ? 9 : (+e.key >= 1 && +e.key <= 9 ? +e.key - 1 : -1);
  if (idx >= 0 && idx < TAB_KEYS.length) activateTab(TAB_KEYS[idx]);
});

// re-render dynamic content when the UI language is toggled (chrome is handled
// by i18n.js applyI18n; this refreshes JS-rendered strings like the inbox title)
document.addEventListener("i18n:changed", () => {
  if (STATE && STATE.started && !STATE.game_over) renderAll();
});

boot();
