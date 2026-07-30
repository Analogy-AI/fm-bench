/* FM Bench — Squad Showcase.
   Renders a REAL procedurally-generated squad from the engine's obs API
   (POST /api/tool get_squad / get_club_overview). Falls back to an embedded
   sample if no server/game is reachable. Faithful to hidden information:
   ability is shown as a RANGE + confidence letter, never a fake exact rating. */

const FLAGS = { // a few unicode flags for flavour; unknowns fall back to a dot
  BRA:"🇧🇷", ARG:"🇦🇷", ENG:"🏴", FRA:"🇫🇷", GER:"🇩🇪", ESP:"🇪🇸", ITA:"🇮🇹",
  POR:"🇵🇹", NED:"🇳🇱", BEL:"🇧🇪", CRO:"🇭🇷", URU:"🇺🇾", MEX:"🇲🇽", USA:"🇺🇸",
  JPN:"🇯🇵", KOR:"🇰🇷", NGA:"🇳🇬", GHA:"🇬🇭", SEN:"🇸🇳", COL:"🇨🇴", CHI:"🇨🇱",
  SWE:"🇸🇪", DEN:"🇩🇰", NOR:"🇳🇴", POL:"🇵🇱", TUR:"🇹🇷", GRE:"🇬🇷", SUI:"🇨🇭",
};
const flag = (n) => FLAGS[n] || "";  // nationalities may be fictional; show name only
const posClass = (p) => (p === "GK" ? "GK"
  : ["DL","DC","DR","CB","LB","RB"].includes(p) || p.startsWith("D") ? "DEF"
  : ["ST","CF","FW","LW","RW"].includes(p) || p.startsWith("F") ? "FWD" : "MID");

async function tool(name, args = {}) {
  const r = await fetch("/api/tool", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, args }),
  });
  if (!r.ok) throw new Error("tool http " + r.status);
  const env = await r.json();
  if (!env.ok) throw new Error(env.error || "tool error");
  return env.data;
}

async function newGame(seed) {
  const r = await fetch("/api/new", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seed: Number(seed), years: 5 }),
  });
  if (!r.ok) throw new Error("new http " + r.status);
  return r.json();
}

/* ---- coarse display-only value tier from the ability band midpoint + age.
   NOT a hidden true value — a presentational estimate derived only from the
   VISIBLE band (0..200 scale) + age, shown as a range. ---- */
function estValue(card) {
  const mid10 = (card.ability.ca_low + card.ability.ca_high) / 2 / 10; // -> ~0..20
  const ageAdj = card.age <= 24 ? 1.15 : card.age >= 31 ? 0.6 : 1.0;
  const base = Math.max(0.2, ((mid10 - 4) ** 2) * 0.06 * ageAdj);
  const lo = base * 0.75, hi = base * 1.3;
  const fmt = (x) => x >= 10 ? x.toFixed(0) : x.toFixed(1);
  return "€" + fmt(lo) + "–" + fmt(hi) + "M";
}

function formDots(form) { // form ~ 0..10
  const out = [];
  for (let i = 0; i < 5; i++) {
    const t = form - i * 2;
    out.push(t >= 1.5 ? "on" : t >= 0.5 ? "mid" : "off");
  }
  return out.map((c) => `<span class="fdot ${c}"></span>`).join("");
}

function playerCard(c, best) {
  const pc = posClass(c.position);
  const b = c.ability, span = (b.ca_high - b.ca_low) || 2;
  // band bar on a 0..200 ability scale: position by ca_low, width ∝ uncertainty
  const left = Math.max(0, Math.min(100, (b.ca_low / 200) * 100));
  const width = Math.max(4, Math.min(100 - left, (span / 200) * 100));
  const badges = [];
  if (c.injury_days > 0) badges.push(`<span class="badge inj">${t("sc.badgeInjured")}</span>`);
  if (c.listed) badges.push(`<span class="badge listed">${t("badge.listed")}</span>`);
  const star = c.youth && c.potential_stars
    ? `<span class="pc-star" title="${t("detail.potential")}">${"★".repeat(Math.round(c.potential_stars))}</span>` : "";
  return `<div class="pcard ${best ? "best" : ""}">
    <div class="pc-top">
      <span class="pc-pos ${pc}">${c.position}</span>
      <div class="pc-id">
        <span class="pc-name">${c.name}</span>
        <span class="pc-sub">${flag(c.nationality)} ${c.nationality} · ${c.age}y</span>
      </div>${star}
    </div>
    <div class="band-row" title="${t("sc.bandTitle", { conf: b.confidence })}">
      <div class="band-track"><div class="band-fill ${b.confidence}"
        style="left:${left}%;width:${width}%"></div></div>
      <span class="band-txt">${b.ca_low}–${b.ca_high}</span>
      <span class="band-conf">${b.confidence}</span>
    </div>
    <div class="pc-foot">
      <span class="form-dots" title="${t("sc.formTitle")}">${formDots(c.form)}</span>
      <span class="pc-val">${estValue(c)}</span>
    </div>
    ${badges.length ? `<div class="pc-foot">${badges.join("")}</div>` : ""}
  </div>`;
}

/* pick a plausible best XI: 1 GK + 4 DEF + 3 MID + 3 FWD, by band midpoint */
function bestXI(squad) {
  const mid = (c) => (c.ability.ca_low + c.ability.ca_high) / 2
    - (c.injury_days > 0 ? 3 : 0);
  const by = (grp) => squad.filter(grp).sort((a, b) => mid(b) - mid(a));
  const gk = by((c) => posClass(c.position) === "GK");
  const df = by((c) => posClass(c.position) === "DEF");
  const md = by((c) => posClass(c.position) === "MID");
  const fw = by((c) => posClass(c.position) === "FWD");
  const pick = [...gk.slice(0, 1), ...df.slice(0, 4), ...md.slice(0, 3), ...fw.slice(0, 3)];
  // top up to 11 from whoever's left by rating
  if (pick.length < 11) {
    const chosen = new Set(pick.map((c) => c.pid));
    const rest = squad.filter((c) => !chosen.has(c.pid)).sort((a, b) => mid(b) - mid(a));
    pick.push(...rest.slice(0, 11 - pick.length));
  }
  return pick.slice(0, 11);
}

/* place XI on the pitch: rows by line, y from GK (bottom) to FWD (top) */
function renderPitch(xi) {
  const lines = { GK: [], DEF: [], MID: [], FWD: [] };
  xi.forEach((c) => lines[posClass(c.position)].push(c));
  const yByLine = { GK: 88, DEF: 66, MID: 44, FWD: 20 };
  const spots = [];
  for (const line of ["GK", "DEF", "MID", "FWD"]) {
    const row = lines[line];
    row.forEach((c, i) => {
      const x = ((i + 1) / (row.length + 1)) * 100;
      const y = yByLine[line];
      const short = c.name.split(" ").slice(-1)[0];
      const rating = Math.round((c.ability.ca_low + c.ability.ca_high) / 2 / 10);
      spots.push(`<div class="spot" style="left:${x}%;top:${y}%">
        <div class="disc pos-${line}" title="${t("sc.spotTitle", { name: c.name, position: c.position, low: c.ability.ca_low, high: c.ability.ca_high })}">${rating}</div>
        <span class="nm">${short}</span></div>`);
    });
  }
  const pitch = document.getElementById("pitch");
  pitch.innerHTML = `<div class="center-circle"></div>` + spots.join("");
}

function renderTeam(ov, seed, live) {
  const mono = (ov.name || "FC").split(" ").map((w) => w[0]).join("").slice(0, 3).toUpperCase();
  document.getElementById("team").innerHTML = `<div class="team-card">
    <div class="crest">${mono}</div>
    <div class="team-meta">
      <span class="tn">${ov.name || t("sc.unknownClub")}</span>
      <span class="tsub">${t("sc.teamMeta", { division: ov.division ?? "?", seed: seed, style: ov.style ?? "—" })}</span>
    </div>
    <div class="team-stats">
      <div class="tstat"><span class="v">${ov.league_position ?? "—"}</span><span class="l">${t("sc.position")}</span></div>
      <div class="tstat"><span class="v">€${ov.cash_m ?? "—"}M</span><span class="l">${t("fin.cash")}</span></div>
      <div class="tstat"><span class="v">${ov.board_confidence ?? "—"}</span><span class="l">${t("sc.board")}</span></div>
      <div class="tstat"><span class="v">${ov.reputation ?? "—"}</span><span class="l">${t("club.reputation")}</span></div>
    </div>
  </div>`;
}

function renderSquad(squad, xi) {
  const best = new Set(xi.map((c) => c.pid));
  const order = (c) => (best.has(c.pid) ? 0 : 1);
  const sorted = [...squad].sort((a, b) =>
    order(a) - order(b) ||
    (b.ability.ca_low + b.ability.ca_high) - (a.ability.ca_low + a.ability.ca_high));
  document.getElementById("grid").innerHTML =
    sorted.map((c) => playerCard(c, best.has(c.pid))).join("");
}

function setSrc(kind) {
  const el = document.getElementById("src");
  el.className = "sc-src " + (kind === "live" ? "live" : "sample");
  el.textContent = kind === "live" ? t("sc.srcLive") : t("sc.srcSample");
}

/* last-rendered squad, kept so a language toggle can re-render dynamic text
   (crest labels, note, tooltips) without re-fetching from the engine. */
let LAST = null;

function render(data) {
  const { ov, squad, seed, live } = data;
  const xi = bestXI(squad);
  setSrc(live ? "live" : "sample");
  renderTeam(ov, seed, live);
  renderPitch(xi);
  renderSquad(squad, xi);
  const note = document.getElementById("note");
  note.textContent = live
    ? t("sc.noteLive", { n: squad.length, seed: seed })
    : t("sc.noteSample");
}

async function load() {
  const seed = document.getElementById("seed").value || "1";
  document.getElementById("note").textContent = "";
  try {
    await newGame(seed);                       // fresh game on this seed
    const squad = await tool("get_squad");     // real obs data
    const ov = await tool("get_club_overview");
    const arr = Array.isArray(squad) ? squad : (squad.squad || squad.players || []);
    LAST = { ov, squad: arr, seed, live: true };
  } catch (e) {
    // graceful fallback: embedded sample so the page still showcases well
    LAST = { ov: SAMPLE.overview, squad: SAMPLE.squad, seed: "—", live: false };
  }
  render(LAST);
}

/* ---- embedded fallback sample (fictional, band-only, hidden-info faithful) ---- */
const SAMPLE = {
  overview: { name: "Velburg City", division: 1, league_position: 4,
    cash_m: 38, board_confidence: 71, reputation: 62, style: "balanced" },
  squad: [
    { pid:"s1", name:"Тomas Reinholt", position:"GK", age:29, nationality:"DEN", form:6.5, injury_days:0, listed:false, youth:false, ability:{ca_low:120,ca_high:130,confidence:"A"} },
    { pid:"s2", name:"Мarco Elvestad", position:"DC", age:27, nationality:"NOR", form:7.0, injury_days:0, listed:false, youth:false, ability:{ca_low:120,ca_high:130,confidence:"A"} },
    { pid:"s3", name:"Léo Fontaine", position:"DC", age:31, nationality:"FRA", form:5.5, injury_days:0, listed:false, youth:false, ability:{ca_low:110,ca_high:120,confidence:"A"} },
    { pid:"s4", name:"Bram de Vries", position:"LB", age:24, nationality:"NED", form:7.5, injury_days:0, listed:false, youth:false, ability:{ca_low:110,ca_high:120,confidence:"A"} },
    { pid:"s5", name:"Kwame Osei", position:"RB", age:26, nationality:"GHA", form:6.0, injury_days:0, listed:false, youth:false, ability:{ca_low:100,ca_high:120,confidence:"A"} },
    { pid:"s6", name:"Diego Marchetti", position:"MC", age:28, nationality:"ITA", form:8.0, injury_days:0, listed:false, youth:false, ability:{ca_low:130,ca_high:140,confidence:"A"} },
    { pid:"s7", name:"Anders Kjær", position:"MC", age:25, nationality:"DEN", form:6.5, injury_days:0, listed:false, youth:false, ability:{ca_low:120,ca_high:130,confidence:"A"} },
    { pid:"s8", name:"Youssef Benali", position:"ML", age:23, nationality:"FRA", form:7.0, injury_days:0, listed:false, youth:false, ability:{ca_low:110,ca_high:130,confidence:"A"} },
    { pid:"s9", name:"Rafael Souza", position:"ST", age:27, nationality:"BRA", form:8.5, injury_days:0, listed:false, youth:false, ability:{ca_low:140,ca_high:150,confidence:"A"} },
    { pid:"s10", name:"Nikola Petrov", position:"ST", age:29, nationality:"CRO", form:6.0, injury_days:12, listed:false, youth:false, ability:{ca_low:120,ca_high:140,confidence:"A"} },
    { pid:"s11", name:"Sol Herrera", position:"RW", age:22, nationality:"ESP", form:7.5, injury_days:0, listed:false, youth:false, ability:{ca_low:110,ca_high:130,confidence:"A"} },
    { pid:"s12", name:"Ivan Kozlov", position:"GK", age:33, nationality:"POL", form:5.0, injury_days:0, listed:true, youth:false, ability:{ca_low:90,ca_high:110,confidence:"A"} },
    { pid:"s13", name:"Malik Traoré", position:"DC", age:20, nationality:"SEN", form:6.5, injury_days:0, listed:false, youth:true, potential_stars:4, ability:{ca_low:80,ca_high:110,confidence:"A"} },
    { pid:"s14", name:"Lucas Meyer", position:"MC", age:19, nationality:"GER", form:6.0, injury_days:0, listed:false, youth:true, potential_stars:3, ability:{ca_low:70,ca_high:100,confidence:"A"} },
    { pid:"s15", name:"Owen Blackwood", position:"ST", age:21, nationality:"ENG", form:7.0, injury_days:0, listed:false, youth:false, ability:{ca_low:100,ca_high:120,confidence:"A"} },
  ],
};

document.getElementById("load").addEventListener("click", load);

// re-render dynamic text when the UI language is toggled (static chrome is
// handled by i18n.js applyI18n; this refreshes JS-rendered strings)
document.addEventListener("i18n:changed", () => { if (LAST) render(LAST); });

load();
