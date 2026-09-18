// ============================================================
// recaps.html client logic
// script.js (loaded before this on the page) handles the shared header
// ticker via loadFeed() -- everything here is specific to this page:
// figuring out which week to show, loading data/previews/<season>-wk<week>.json,
// and prev/next navigation via the URL's ?season=&week= params.
// ============================================================

// RECAPS_SLEEPER_LEAGUE_ID resolves independently from data/league_config.json
// rather than reading script.js's own SLEEPER_LEAGUE_ID -- both files load
// this same JSON, but script.js resolves it inside its own async
// DOMContentLoaded handler, and this file's DOMContentLoaded handler could
// start running before that resolves (handlers fire in registration order,
// but an async handler yields at its first await, so "script.js's listener
// runs first" doesn't guarantee "script.js's listener FINISHES first").
// Fetching it again here avoids that race entirely; the browser's HTTP
// cache makes the second fetch essentially free.
//
// NOT named SLEEPER_LEAGUE_ID on purpose -- script.js (loaded before this
// on the page) already declares that exact name at top level, and browser
// <script> tags share one global scope, unlike the Python scripts' separate
// processes. Redeclaring it here threw a SyntaxError that silently killed
// this entire file before anything in it could run -- caught from a real
// bug report, not a guess.
let RECAPS_SLEEPER_LEAGUE_ID = null;
const SLEEPER_API = "https://api.sleeper.app/v1";

async function loadRecapsLeagueConfig() {
  const res = await fetch("data/league_config.json");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// escapeHtml() is intentionally NOT redeclared here -- script.js (loaded
// first on this page) already defines it, and reusing it avoids the same
// class of naming mistake that caused the SLEEPER_LEAGUE_ID bug above,
// even though a duplicate function declaration wouldn't have been fatal
// the way the duplicate const was.

function getWeekFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const week = parseInt(params.get("week"), 10);
  const season = parseInt(params.get("season"), 10);
  return {
    week: Number.isFinite(week) ? week : null,
    season: Number.isFinite(season) ? season : null,
  };
}

function setWeekInUrl(season, week) {
  const params = new URLSearchParams(window.location.search);
  params.set("season", season);
  params.set("week", week);
  history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
}

async function detectDefaultSeasonWeek() {
  // Simplified client-side stand-in for compute_power_stats.py's
  // resolve_weeks() -- that function is phase-aware (regular season vs.
  // playoffs) and handles several edge cases server-side; this only
  // covers the common in-season case (target/preview week == Sleeper's
  // current state.week), which is what this page needs for a sensible
  // default landing view. Not a full port on purpose.
  try {
    const [league, state] = await Promise.all([
      fetch(`${SLEEPER_API}/league/${RECAPS_SLEEPER_LEAGUE_ID}`).then(r => r.json()),
      fetch(`${SLEEPER_API}/state/nfl`).then(r => r.json()),
    ]);
    const week = state.season_type === "pre" ? 1 : state.week;
    return { season: league.season, week };
  } catch {
    return { season: new Date().getFullYear(), week: 1 };
  }
}

async function buildAvatarLookup() {
  // team_name -> avatarId, same resolution order used everywhere else in
  // this pipeline (Python and JS alike): metadata.team_name falls back to
  // display_name, metadata.avatar falls back to the account avatar. Reuses
  // script.js's avatarImg() to actually render it -- same reasoning as
  // reusing escapeHtml() above, not reimplementing a second copy.
  //
  // Also returns roster_id -> team_name, needed by the Season Plots
  // power-rank chart below: data/power_log.json is keyed by roster_id
  // with no team names in it at all, so this is the only place that
  // mapping exists on this page. One shared fetch backs both lookups
  // rather than hitting Sleeper twice for the same users/rosters.
  try {
    const [users, rosters] = await Promise.all([
      fetch(`${SLEEPER_API}/league/${RECAPS_SLEEPER_LEAGUE_ID}/users`).then(r => r.json()),
      fetch(`${SLEEPER_API}/league/${RECAPS_SLEEPER_LEAGUE_ID}/rosters`).then(r => r.json()),
    ]);
    const userById = {};
    users.forEach(u => { userById[u.user_id] = u; });

    const lookup = {};
    const teamNameByRosterId = {};
    rosters.forEach(r => {
      const u = userById[r.owner_id];
      if (!u) return;
      const teamName = (u.metadata && u.metadata.team_name) || u.display_name;
      const avatarId = (u.metadata && u.metadata.avatar) || u.avatar || null;
      if (teamName) {
        lookup[teamName] = avatarId;
        teamNameByRosterId[r.roster_id] = teamName;
      }
    });
    return { lookup, teamNameByRosterId };
  } catch {
    return { lookup: {}, teamNameByRosterId: {} }; // avatarImg() already renders a graceful empty placeholder for a missing id
  }
}

function avatarUrl(avatarId) {
  // Mirrors avatarImg()'s URL resolution above -- duplicated rather than
  // shared because SVG <image> needs a bare URL string, not the <img>
  // markup avatarImg() returns. Keep both in sync if Sleeper's avatar URL
  // scheme ever changes.
  if (!avatarId) return null;
  return /^https?:\/\//i.test(avatarId) ? avatarId : `https://sleepercdn.com/avatars/thumbs/${avatarId}`;
}

function renderMatchup(m, avatarLookup) {
  const avatarA = avatarImg(avatarLookup[m.team_a], m.team_a);
  const avatarB = avatarImg(avatarLookup[m.team_b], m.team_b);
  return `
    <div class="card preview-card">
      <p class="preview-teams">
        ${avatarA} ${escapeHtml(m.team_a)} <span class="preview-vs">vs</span> ${avatarB} ${escapeHtml(m.team_b)}
      </p>
      <p class="preview-text">${escapeHtml(m.preview)}</p>
    </div>`;
}

async function loadPreviews(season, week, avatarLookup) {
  const heading = document.getElementById("week-heading");
  const list = document.getElementById("preview-list");
  // No longer "Week N Previews" -- this heading now sits above both the
  // Recap and Preview sections, and Recap shows a DIFFERENT week number
  // (see loadRecap below), so a preview-specific label here would be
  // actively misleading rather than just incomplete.
  heading.textContent = `Week ${week}`;
  list.innerHTML = `<p class="loading-msg">Loading previews&hellip;</p>`;

  try {
    const res = await fetch(`data/previews/${season}-wk${week}.json`);
    if (!res.ok) throw new Error(`No preview generated for Week ${week} yet`);
    const data = await res.json();
    if (!data.matchups || data.matchups.length === 0) {
      list.innerHTML = `<p class="loading-msg">No matchups found for Week ${week}.</p>`;
      return;
    }
    list.innerHTML = data.matchups.map(m => renderMatchup(m, avatarLookup)).join("");
  } catch (err) {
    list.innerHTML = `<p class="loading-msg">${escapeHtml(err.message)}</p>`;
  }
}

// ============================================================
// Recap awards -- reads data/recaps/<season>-wk<week>.json, generated by
// generate_recap_awards.py + render_recap_awards.py.
//
// IMPORTANT: the "week" in the URL/nav is the PREVIEW week (the upcoming
// matchups, per detectDefaultSeasonWeek()'s use of Sleeper's current
// state.week). The recap on the same page is for the week that JUST
// finished -- one week EARLIER -- matching resolve_weeks() server-side:
// forecast_weeks[0] == state.week, and the last completed week is always
// state.week - 1 during a normal in-season Tuesday run. So this
// deliberately fetches wk<week - 1>, not wk<week>, even though the
// preview section right below it fetches wk<week> from a sibling
// directory. Not a typo -- see PROJECT_HANDOFF.md / recaps-roadmap.md for
// why the two pipelines target different weeks on purpose.
//
// Reuses the preview-card/preview-teams/preview-text classes rather than
// inventing a parallel set of award-card classes purely for this -- the
// visual treatment (a bordered card, a bold first line, a prose line
// under it) is identical, just with an award name instead of a matchup
// header. No style.css changes needed.
function renderAward(award) {
  return `
    <div class="card preview-card">
      <p class="preview-teams">${escapeHtml(award.name)}</p>
      <p class="preview-text">${escapeHtml(award.text)}</p>
    </div>`;
}

async function loadRecap(season, week) {
  const list = document.getElementById("recap-list");
  const recapWeek = week - 1;

  if (recapWeek < 1) {
    list.innerHTML = `<p class="loading-msg">No games played yet this season.</p>`;
    return;
  }

  list.innerHTML = `<p class="loading-msg">Loading recap&hellip;</p>`;
  try {
    const res = await fetch(`data/recaps/${season}-wk${recapWeek}.json`);
    if (!res.ok) throw new Error(`No recap generated for Week ${recapWeek} yet`);
    const data = await res.json();

    // context is a short "how the week went" lede, distinct from the
    // per-award cards below it -- rendered first if present. Either can
    // independently be empty (a week can have awards but no notable
    // league-wide superlative, or vice versa isn't expected but handled
    // the same defensive way), so only show the empty-state message if
    // BOTH are missing.
    const contextHtml = data.context ? `<p class="recap-context">${escapeHtml(data.context)}</p>` : "";
    const awardsHtml = (data.awards && data.awards.length > 0) ? data.awards.map(renderAward).join("") : "";

    if (!contextHtml && !awardsHtml) {
      list.innerHTML = `<p class="loading-msg">No recap content for Week ${recapWeek}.</p>`;
      return;
    }
    list.innerHTML = contextHtml + awardsHtml;
  } catch (err) {
    list.innerHTML = `<p class="loading-msg">${escapeHtml(err.message)}</p>`;
  }
}

async function init() {
  try {
    const config = await loadRecapsLeagueConfig();
    RECAPS_SLEEPER_LEAGUE_ID = config.sleeper_league_id;
  } catch (err) {
    console.error("Couldn't load data/league_config.json:", err);
    const list = document.getElementById("preview-list");
    if (list) list.innerHTML = `<p class="loading-msg">Couldn't load site configuration (${escapeHtml(err.message)}).</p>`;
    return;
  }

  let { season, week } = getWeekFromUrl();
  const lookupsPromise = buildAvatarLookup(); // kick off in parallel, don't block week detection on it

  if (season === null || week === null) {
    const defaults = await detectDefaultSeasonWeek();
    season = season ?? defaults.season;
    week = week ?? defaults.week;
  }
  setWeekInUrl(season, week);

  const { lookup: avatarLookup, teamNameByRosterId } = await lookupsPromise;
  loadPreviews(season, week, avatarLookup);
  loadRecap(season, week);
  loadSeasonPlots(teamNameByRosterId, avatarLookup); // independent of week -- always current season

  document.getElementById("prev-week-btn").addEventListener("click", () => {
    if (week <= 1) return;
    week -= 1;
    setWeekInUrl(season, week);
    loadPreviews(season, week, avatarLookup);
    loadRecap(season, week);
  });

  document.getElementById("next-week-btn").addEventListener("click", () => {
    week += 1;
    setWeekInUrl(season, week);
    loadPreviews(season, week, avatarLookup);
    loadRecap(season, week);
  });
}

document.addEventListener("DOMContentLoaded", init);

// ============================================================
// Season Plots -- independent of the week nav above; always shows the
// CURRENT season state (recaps-roadmap.md Section 6b, "settled": one
// live data source, not tied to whichever week's recap/preview is being
// viewed elsewhere on this page). Reads data/season_plots.json (Lady
// Luck/Xwins, positional PPW, flex distribution -- computed by
// compute_season_plots.py) and data/power_log.json directly for the
// power-rank-history chart. That file is NOT recomputed here -- it
// already exists, written weekly by compute_power_stats.py, so
// repackaging it into season_plots.json would just create a second
// source of truth for the same numbers.
//
// Charts are hand-built SVG/HTML, not a charting library -- there's no
// charting precedent anywhere else on this site, and a library's
// canvas-based theming fights transparent backgrounds + CSS custom
// properties more than it helps for four fairly simple chart shapes.
// Every stroke/fill below is a var(--...) reference, so retinting
// style.css's tokens re-themes these charts automatically.
// ============================================================

// Deterministic "random" choice, seeded by a string key -- same purpose
// as the Python render scripts' pick(random.Random(seed_key).choice(...)):
// re-rendering the same week's data shouldn't reshuffle the wording every
// time, but different teams/weeks/categories should still get variety.
// JS has no built-in seedable Math.random, so a small string hash
// standing in for one is enough here -- this only ever needs a single
// deterministic pick per seed, not a reproducible sequence of draws.
function pick(options, seedKey) {
  let hash = 0;
  for (let i = 0; i < seedKey.length; i++) {
    hash = (hash * 31 + seedKey.charCodeAt(i)) | 0;
  }
  return options[Math.abs(hash) % options.length];
}

function niceDomain(values, pad = 1) {
  const min = Math.min(...values), max = Math.max(...values);
  return [Math.floor(min - pad), Math.ceil(max + pad)];
}

function scaleLinear(domain, range) {
  const span = (domain[1] - domain[0]) || 1;
  return (v) => range[0] + ((v - domain[0]) / span) * (range[1] - range[0]);
}

function domId(prefix, raw) {
  return `${prefix}-${String(raw)}`.replace(/[^a-zA-Z0-9_-]/g, "");
}

// ---------- Lady Luck: actual wins vs. expected wins (Xwins) ----------

function renderLadyLuck(teams, avatarByTeamName) {
  const entries = Object.values(teams || {});
  if (entries.length === 0) return `<p class="loading-msg">No data yet.</p>`;

  // Tight, non-integer-snapped padding (0.1, not niceDomain's whole-number
  // pad-by-1) -- early season, wins/xwins both sit in [0,1], and a whole-
  // integer pad wastes most of the chart on empty space. Stays correct
  // later in the season too: it just tracks whatever range the data
  // actually spans, plus a small margin, rather than a fixed literal.
  const rawVals = entries.flatMap(t => [t.wins, t.xwins]);
  const domMin = Math.min(...rawVals) - 0.1;
  const domMax = Math.max(...rawVals) + 0.1;
  const W = 560, H = 360, M = { top: 12, right: 16, bottom: 44, left: 34 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;
  const sx = scaleLinear([domMin, domMax], [0, plotW]);
  const sy = scaleLinear([domMin, domMax], [plotH, 0]);

  const ticks = [];
  for (let v = Math.ceil(domMin); v <= Math.floor(domMax); v++) ticks.push(v);

  const gridLines = ticks.map(v => `
    <line class="chart-grid-line" x1="${sx(v)}" y1="0" x2="${sx(v)}" y2="${plotH}" />
    <line class="chart-grid-line" x1="0" y1="${sy(v)}" x2="${plotW}" y2="${sy(v)}" />`).join("");

  const tickLabels = ticks.map(v => `
    <text class="chart-axis-label" x="${sx(v)}" y="${plotH + 16}" text-anchor="middle">${v}</text>
    <text class="chart-axis-label" x="-8" y="${sy(v) + 3}" text-anchor="end">${v}</text>`).join("");

  const diagonal = `<line x1="${sx(domMin)}" y1="${sy(domMin)}" x2="${sx(domMax)}" y2="${sy(domMax)}"
    stroke="var(--chalk-dim)" stroke-width="1" stroke-dasharray="4 4" />`;

  const points = entries.map(t => {
    const cx = sx(t.xwins), cy = sy(t.wins);
    const url = avatarUrl((avatarByTeamName || {})[t.team_name]);
    const clipId = domId("luck-clip", t.team_name);
    const deltaLabel = `${t.luck_delta >= 0 ? "+" : ""}${t.luck_delta}`;
    const img = url ? `
      <clipPath id="${clipId}"><circle cx="${cx}" cy="${cy}" r="11" /></clipPath>
      <image href="${url}" x="${cx - 11}" y="${cy - 11}" width="22" height="22"
             clip-path="url(#${clipId})" onerror="this.remove()" />` : "";
    return `
      <g>
        <circle cx="${cx}" cy="${cy}" r="11" fill="var(--marker)" opacity="0.28" />
        ${img}
        <circle cx="${cx}" cy="${cy}" r="11" fill="none" stroke="var(--field)" stroke-width="1.5" />
        <title>${escapeHtml(t.team_name)}: ${t.wins} actual wins vs ${t.xwins} expected (${deltaLabel})</title>
      </g>`;
  }).join("");

  return `
    <svg class="chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Lady Luck: actual wins versus expected wins">
      <g transform="translate(${M.left},${M.top})">
        ${gridLines}
        ${diagonal}
        ${tickLabels}
        ${points}
        <text class="chart-axis-label" x="${plotW / 2}" y="${plotH + 34}" text-anchor="middle">EXPECTED WINS (XWINS)</text>
        <text class="chart-axis-label" x="${-plotH / 2}" y="-24" text-anchor="middle" transform="rotate(-90)">ACTUAL WINS</text>
      </g>
    </svg>
    <p class="chart-caption">Above the dashed line: winning more than their weekly scores alone would predict. Below it: the reverse.</p>`;
}

// ---------- Positional PPW: ranked mini bar-lists (HTML/CSS, not SVG --
// a ranked list is simpler and more legible this way than as bar-chart SVG) ----------

const PPW_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"];

function renderPositionalPpw(teams) {
  const entries = Object.values(teams || {});
  const panels = PPW_POSITIONS.map(pos => {
    const rows = entries
      .filter(t => t.positional_ppw && t.positional_ppw[pos] != null)
      .map(t => ({ name: t.team_name, value: t.positional_ppw[pos] }))
      .sort((a, b) => b.value - a.value);
    if (rows.length === 0) return "";
    const max = rows[0].value || 1;
    const rowsHtml = rows.map(r => `
      <div class="ppw-row">
        <span class="ppw-row-name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</span>
        <span class="ppw-row-bar-track"><span class="ppw-row-bar-fill" style="width:${Math.max(4, (r.value / max) * 100)}%"></span></span>
        <span class="ppw-row-value">${r.value.toFixed(1)}</span>
      </div>`).join("");
    return `
      <div class="ppw-panel">
        <p class="ppw-panel-title">${pos}</p>
        ${rowsHtml}
      </div>`;
  }).join("");

  return panels || `<p class="loading-msg">No positional data yet.</p>`;
}

// ---------- Flex distribution: single stacked bar (HTML/CSS, not SVG) ----------

// Colors pulled from --chart-1..4 (defined in style.css) rather than
// hardcoded here -- RB/WR/TE/QB are the only flex-eligible positions
// (see compute_season_plots.py's FLEX_ELIGIBLE), so 4 categories is the
// ceiling, never more.
const FLEX_COLORS = { RB: "var(--chart-1)", WR: "var(--chart-2)", TE: "var(--chart-3)", QB: "var(--chart-4)" };
const FLEX_ORDER = ["RB", "WR", "TE", "QB"];

function renderFlexDistribution(flexDistribution) {
  const entries = FLEX_ORDER.filter(pos => (flexDistribution || {})[pos] != null);
  if (entries.length === 0) return `<p class="loading-msg">No flex starts recorded yet.</p>`;

  const segments = entries.map(pos => `<span class="flex-bar-segment" style="width:${flexDistribution[pos]}%; background:${FLEX_COLORS[pos]}"></span>`).join("");
  const legend = entries.map(pos => `
    <span class="chart-legend-item">
      <span class="chart-legend-swatch" style="background:${FLEX_COLORS[pos]}"></span>
      ${pos} &middot; ${flexDistribution[pos]}%
    </span>`).join("");

  return `<div class="flex-bar-track">${segments}</div><div class="chart-legend">${legend}</div>`;
}

// ---------- Power rankings history: multi-line, hover-highlight one team at a time ----------

function renderPowerRankHistory(logEntries, season, teamNameByRosterId, avatarByTeamName) {
  const seasonEntries = (logEntries || []).filter(e => e.season === season).sort((a, b) => a.week - b.week);
  if (seasonEntries.length === 0) return `<p class="loading-msg">No power-rank history logged yet.</p>`;

  const rosterIds = new Set();
  seasonEntries.forEach(e => Object.keys(e.teams).forEach(tid => rosterIds.add(tid)));

  const series = Array.from(rosterIds).map(tid => ({
    tid,
    teamName: (teamNameByRosterId || {})[tid] || `Roster ${tid}`,
    points: seasonEntries
      .filter(e => e.teams[tid] && e.teams[tid].power_score != null)
      .map(e => ({ week: e.week, score: e.teams[tid].power_score })),
  })).filter(s => s.points.length > 0);
  if (series.length === 0) return `<p class="loading-msg">No power-rank history logged yet.</p>`;

  const weeks = seasonEntries.map(e => e.week);
  const [wMin, wMax] = [Math.min(...weeks), Math.max(...weeks)];
  const [sMin, sMax] = niceDomain(series.flatMap(s => s.points.map(p => p.score)), 2);

  const W = 560, H = 340, M = { top: 12, right: 16, bottom: 32, left: 34 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;
  const sx = wMin === wMax ? () => plotW / 2 : scaleLinear([wMin, wMax], [0, plotW]);
  const sy = scaleLinear([sMin, sMax], [plotH, 0]);

  const weekTicks = [];
  for (let w = wMin; w <= wMax; w++) weekTicks.push(w);
  const gridLines = weekTicks.map(w => `<line class="chart-grid-line" x1="${sx(w)}" y1="0" x2="${sx(w)}" y2="${plotH}" />`).join("");
  const tickLabels = weekTicks.map(w => `<text class="chart-axis-label" x="${sx(w)}" y="${plotH + 16}" text-anchor="middle">W${w}</text>`).join("");

  const lines = series.map(s => {
    const avatarSrc = avatarUrl((avatarByTeamName || {})[s.teamName]);
    const coords = s.points.map(p => ({ week: p.week, x: sx(p.week), y: sy(p.score) }));
    // A <polyline> needs 2+ points to draw any visible stroke at all --
    // with only one week logged so far, that's every series right now.
    // Dots are drawn for every point regardless, so the very first
    // logged week is already visible, not just once there's enough
    // history for a line to connect.
    const polyline = coords.length > 1
      ? `<polyline points="${coords.map(c => `${c.x},${c.y}`).join(" ")}" />`
      : "";
    const dots = coords.map(c => renderPowerRankDot(c.x, c.y, avatarSrc, domId("pr-clip", `${s.tid}-${c.week}`))).join("");
    return `<g id="${domId("pr", s.tid)}" class="power-rank-line">${polyline}${dots}<title>${escapeHtml(s.teamName)}</title></g>`;
  }).join("");

  const legend = series.map(s => `<span class="chart-legend-item" data-target="${domId("pr", s.tid)}">${escapeHtml(s.teamName)}</span>`).join("");

  return `
    <svg class="chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Power score history by week">
      <g transform="translate(${M.left},${M.top})">
        ${gridLines}
        ${tickLabels}
        ${lines}
      </g>
    </svg>
    <div class="chart-legend" id="power-rank-legend">${legend}</div>`;
}

function renderPowerRankDot(x, y, avatarSrc, clipId) {
  // Same layered approach as Lady Luck's markers: a colored fallback
  // circle drawn first (always visible), the team's avatar clipped on
  // top if one resolves, and a thin ring on top of both -- the ring is
  // what actually brightens on hover (a raster image can't be recolored
  // via CSS the way a <circle fill> can).
  const r = 7, innerR = r - 1.5;
  const img = avatarSrc ? `
    <clipPath id="${clipId}"><circle cx="${x}" cy="${y}" r="${innerR}" /></clipPath>
    <image href="${avatarSrc}" x="${x - innerR}" y="${y - innerR}" width="${innerR * 2}" height="${innerR * 2}"
           clip-path="url(#${clipId})" onerror="this.remove()" />` : "";
  return `
    <circle class="pr-dot-fallback" cx="${x}" cy="${y}" r="${innerR}" />
    ${img}
    <circle class="pr-dot-ring" cx="${x}" cy="${y}" r="${r}" />`;
}

function wirePowerRankHover(container) {
  // Plain event listeners rather than a :has()-based CSS trick -- matches
  // this codebase's existing preference (see the lifetime-toggle switch
  // in style.css) for explicit, broadly-supported behavior over newer
  // selector tricks.
  const legend = container.querySelector("#power-rank-legend");
  if (!legend) return;
  legend.querySelectorAll(".chart-legend-item").forEach(item => {
    const line = container.querySelector(`#${item.dataset.target}`);
    if (!line) return;
    const activate = () => { line.classList.add("is-active"); item.classList.add("is-active"); };
    const deactivate = () => { line.classList.remove("is-active"); item.classList.remove("is-active"); };
    item.addEventListener("mouseenter", activate);
    item.addEventListener("mouseleave", deactivate);
    line.addEventListener("mouseenter", activate);
    line.addEventListener("mouseleave", deactivate);
  });
}

// ---------- What-If Schedule: same Xwins computation as Lady Luck, table
// form instead of a scatter -- distinct plot per recaps-roadmap.md
// Section 6b/6c ("what-if schedule" and "Lady Luck" are listed as two
// separate season plots, not one). A table reads exact numbers more
// easily than a scatter, and -- per that same section -- can render
// meaningfully at Week 1 (0-0 for everyone) even when a scatter plot of
// all-zero points wouldn't be worth drawing. No new backend computation
// needed: season_plots.json's wins/losses/ties/xwins/luck_delta already
// have everything this table needs. ----------

function formatRecord(wins, losses, ties) {
  return ties ? `${wins}-${losses}-${ties}` : `${wins}-${losses}`;
}

function renderWhatIfSchedule(teams, weeksComplete) {
  const entries = Object.values(teams || {}).sort((a, b) => b.luck_delta - a.luck_delta);
  if (entries.length === 0) return `<p class="loading-msg">No data yet.</p>`;

  const rows = entries.map(t => {
    const whatIfWins = t.xwins;
    const whatIfLosses = Math.max(0, (weeksComplete || 0) - t.xwins);
    const deltaClass = t.luck_delta > 0 ? "whatif-luck-positive" : "whatif-luck-neutral";
    const deltaLabel = `${t.luck_delta >= 0 ? "+" : ""}${t.luck_delta.toFixed(1)}`;
    return `
      <tr>
        <td class="whatif-team-cell">${escapeHtml(t.team_name)}</td>
        <td>${formatRecord(t.wins, t.losses, t.ties)}</td>
        <td>${whatIfWins.toFixed(1)}-${whatIfLosses.toFixed(1)}</td>
        <td class="${deltaClass}">${deltaLabel}</td>
      </tr>`;
  }).join("");

  return `
    <table class="whatif-real-table">
      <thead>
        <tr>
          <th>Team</th>
          <th>Actual</th>
          <th>What-If</th>
          <th>Luck</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="chart-caption">"What-If" is the record each team's weekly scores alone would predict, ignoring who they actually played -- the same Xwins used in Lady Luck above, just as exact numbers instead of a scatter.</p>`;
}

// ---------- What-If Schedule GRID: the full pairwise swap ("if manager A
// had played manager B's exact schedule"), distinct from the closed-form
// table above. Every cell comes straight from compute_season_plots.py's
// what_if_grid (no client-side computation) -- this just colors and lays
// it out. Colors are read live from CSS custom properties rather than
// hardcoded here, so retinting style.css re-themes this heatmap too. ----------

function hexToRgbArr(hex) {
  const n = parseInt(hex.replace("#", "").trim(), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mixColor(hexA, hexB, t) {
  const a = hexToRgbArr(hexA), b = hexToRgbArr(hexB);
  const clamped = Math.max(0, Math.min(1, t));
  return `rgb(${a.map((c, i) => Math.round(c + (b[i] - c) * clamped)).join(", ")})`;
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function renderWhatIfGrid(grid) {
  const managers = (grid && grid.managers) || [];
  if (managers.length === 0) return `<p class="loading-msg">No data yet.</p>`;

  const allDeltas = managers.flatMap(a => managers.map(b => grid.cells[a][b].delta));
  const maxAbsDelta = Math.max(1, ...allDeltas.map(Math.abs)); // avoid divide-by-zero before any delta exists

  const neutral = cssVar("--field-light", "#16332a");
  const positive = cssVar("--marker", "#ff6a13");
  const negative = cssVar("--chart-cold", "#5b7a94");

  const headerRow = managers.map(b => `<th>${escapeHtml(grid.team_names[b] || b)}</th>`).join("");

  const bodyRows = managers.map(a => {
    const cells = managers.map(b => {
      const cell = grid.cells[a][b];
      const t = (Math.abs(cell.delta) / maxAbsDelta) * 0.8; // cap mix intensity so chalk text stays legible
      const bg = cell.delta > 0 ? mixColor(neutral, positive, t)
        : cell.delta < 0 ? mixColor(neutral, negative, t)
        : neutral;
      const record = cell.ties ? `${cell.wins}-${cell.losses}-${cell.ties}` : `${cell.wins}-${cell.losses}`;
      const deltaLabel = `${cell.delta >= 0 ? "+" : ""}${cell.delta}`;
      return `<td style="background:${bg}"><div class="whatif-grid-record">${record}</div><div class="whatif-grid-delta">${deltaLabel}</div></td>`;
    }).join("");
    return `<tr><th class="whatif-grid-rowhead">${escapeHtml(grid.team_names[a] || a)}</th>${cells}</tr>`;
  }).join("");

  return `
    <table class="whatif-grid-table">
      <thead><tr><th class="whatif-grid-corner">had this team's schedule &rarr;</th>${headerRow}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
    <p class="chart-caption">Each row's actual weekly scores, replayed against each column's actual opponents. The diagonal is always a team's real record. Orange: better than they actually did. Blue: worse.</p>`;
}

// ---------- assembly ----------

// ---------- Current Power Rankings: latest week, ranked, with a
// rank-movement arrow vs. the previous logged week. Omitted entirely when
// there's no previous week to compare against (e.g. the season's first
// logged week) -- per the request, rather than showing a meaningless
// "same" for every team. ----------

// ---------- Power ranking blurbs: for each team, pick the single most
// notable thing about them this week (streak, luck, a positional
// strength/weakness, an extreme weekly finish, or a big power-rank jump)
// and render one sentence from a phrasing bank -- mined from the
// "Week N Power Rankings" one-liners across both seasons of real
// write-ups. Falls back to a plain record statement if nothing this
// team did is notable enough to headline (matches every other part of
// this pipeline: compute what's TRUE, render only what's actually
// worth saying). ----------

function _ordinal(n) {
  n = Math.round(n);
  const mod100 = n % 100;
  if (mod100 >= 10 && mod100 <= 20) return `${n}th`;
  const suffix = { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th";
  return `${n}${suffix}`;
}

function computeBlurbCandidates(teamData, numTeams, moveDirection, moveAmount, benchRank) {
  const candidates = [];

  const streak = teamData.streak;
  if (streak && streak.length >= 3 && (streak.type === "W" || streak.type === "L")) {
    candidates.push({ type: streak.type === "W" ? "streak_win" : "streak_loss", notability: streak.length, data: { length: streak.length } });
  }

  const luck = teamData.luck_delta;
  if (luck != null && Math.abs(luck) >= 1.0) {
    candidates.push({ type: luck > 0 ? "lucky" : "unlucky", notability: Math.abs(luck) * 2, data: { delta: Math.abs(luck) } });
  }

  // Widened from "must be exactly #1 / dead-last" to top-2 / bottom-2 --
  // strict #1-only was so rare across a 12-team, 6-position field that
  // almost nobody ever qualified, which was a real gap, not just an
  // early-season artifact.
  const posRanks = teamData.positional_ranks || {};
  const posEntries = Object.entries(posRanks);
  if (posEntries.length > 0) {
    const best = posEntries.reduce((a, b) => (b[1] < a[1] ? b : a));
    if (best[1] <= 2) candidates.push({ type: "strength", notability: best[1] === 1 ? 6 : 4.5, data: { position: best[0], isBest: best[1] === 1 } });
    const worst = posEntries.reduce((a, b) => (b[1] > a[1] ? b : a));
    if (numTeams > 1 && worst[1] >= numTeams - 1) candidates.push({ type: "weakness", notability: worst[1] === numTeams ? 5 : 3.5, data: { position: worst[0], isWorst: worst[1] === numTeams } });
  }

  // Weekly finish -- graded and ALMOST ALWAYS available (as soon as one
  // week exists), so this is what actually replaces the flat "sit at
  // X-Y" fallback most weeks, not a rare category. Notability scales
  // with distance from either edge: a true middle-of-the-pack week still
  // beats the fully generic fallback (every team gets a real, specific
  // sentence about their actual week), but easily loses to a genuine
  // streak/luck/positional story once a season has enough history for
  // those to exist.
  const rank = teamData.last_week_rank, score = teamData.last_week_score;
  if (rank != null && score != null && numTeams > 1) {
    const distFromEdge = Math.min(rank - 1, numTeams - rank);
    candidates.push({ type: "weekly_finish", notability: Math.max(2, 10 - distFromEdge * 2), data: { rank, score, numTeams } });
  }

  if (teamData.last_week_bench_points != null && benchRank != null && benchRank <= 2) {
    candidates.push({ type: "bench", notability: benchRank === 1 ? 4.5 : 3.5, data: { points: teamData.last_week_bench_points } });
  }

  if (moveAmount >= 3) candidates.push({ type: moveDirection === "up" ? "rank_jump_up" : "rank_jump_down", notability: moveAmount, data: { amount: moveAmount } });

  candidates.sort((a, b) => b.notability - a.notability);
  return candidates;
}

function blurbVariants(candidate, teamName) {
  if (!candidate) return null;
  const d = candidate.data;
  switch (candidate.type) {
    case "streak_win": return [
      `${teamName} are riding a ${d.length}-game winning streak.`,
      `${d.length} in a row now for ${teamName}.`,
    ];
    case "streak_loss": return [
      `${teamName} have dropped ${d.length} straight.`,
      `${teamName} are stuck in a ${d.length}-game skid.`,
    ];
    case "lucky": return [
      `${teamName} have more wins than their weekly scores alone would suggest -- ${d.delta.toFixed(1)} above expectation.`,
      `Fortune's been kind to ${teamName}: ${d.delta.toFixed(1)} wins clear of their Xwins pace.`,
    ];
    case "unlucky": return [
      `${teamName}'s record doesn't reflect how they've actually played -- ${d.delta.toFixed(1)} wins below expectation.`,
      `The numbers say ${teamName} have been unlucky: ${d.delta.toFixed(1)} wins off their Xwins pace.`,
    ];
    case "strength": return d.isBest ? [
      `${teamName} boast the league's best ${d.position} corps.`,
      `No one beats ${teamName} at ${d.position} this season.`,
    ] : [
      `${teamName} have one of the league's best ${d.position} rooms.`,
      `${teamName} rank near the top of the league at ${d.position}.`,
    ];
    case "weakness": return d.isWorst ? [
      `${teamName}'s ${d.position} room has been the league's worst.`,
      `${d.position} remains the clear hole in ${teamName}'s roster.`,
    ] : [
      `${teamName}'s ${d.position} room has struggled, near the bottom of the league.`,
      `${d.position} is proving to be a soft spot for ${teamName}.`,
    ];
    case "weekly_finish": {
      const scoreStr = d.score.toFixed(1);
      if (d.rank === 1) return [
        `${teamName} posted the week's best score (${scoreStr} points).`,
        `Nobody outscored ${teamName} this week (${scoreStr}).`,
      ];
      if (d.rank === d.numTeams) return [
        `${teamName} finished with the week's worst score (${scoreStr} points).`,
        `${teamName} brought up the rear this week with ${scoreStr} points.`,
      ];
      if (d.rank <= 3) return [
        `${teamName} finished ${_ordinal(d.rank)} on the week with ${scoreStr} points.`,
        `${teamName} was one of the week's top scorers (${scoreStr}, ${_ordinal(d.rank)}).`,
      ];
      if (d.rank >= d.numTeams - 2) return [
        `${teamName} scored just ${scoreStr} points -- ${_ordinal(d.numTeams - d.rank + 1)}-worst this week.`,
        `${teamName} struggled to ${scoreStr} points this week.`,
      ];
      return [
        `${teamName} scored ${scoreStr} points this week, good for ${_ordinal(d.rank)}.`,
        `${teamName} finished ${_ordinal(d.rank)} on the week (${scoreStr} points).`,
      ];
    }
    case "bench": return [
      `${teamName} left ${d.points.toFixed(1)} points on the bench this week.`,
      `${teamName}'s bench outscored plenty of starting lineups -- ${d.points.toFixed(1)} points left unused.`,
    ];
    case "rank_jump_up": return [
      `${teamName} climbed ${d.amount} spots in the power rankings.`,
      `${teamName} are on the move, up ${d.amount} spots this week.`,
    ];
    case "rank_jump_down": return [
      `${teamName} tumbled ${d.amount} spots this week.`,
      `A rough week sends ${teamName} down ${d.amount} spots.`,
    ];
    default: return null;
  }
}

function renderPowerRankBlurb(teamName, teamData, numTeams, moveDirection, moveAmount, benchRank, seedKey) {
  const candidates = computeBlurbCandidates(teamData || {}, numTeams, moveDirection, moveAmount, benchRank);
  let variants = blurbVariants(candidates[0], teamName);
  if (!variants) {
    // Generic fallback -- should now be rare (only when we don't even
    // know last week's rank/score for this team), but always available
    // so no row is ever left blank.
    const record = teamData && teamData.ties ? `${teamData.wins}-${teamData.losses}-${teamData.ties}` : `${(teamData || {}).wins}-${(teamData || {}).losses}`;
    variants = [`${teamName} sit at ${record} on the season.`];
  }
  return pick(variants, seedKey);
}

function renderCurrentPowerRankings(logEntries, season, teamNameByRosterId, avatarByTeamName, plotTeams) {
  const seasonEntries = (logEntries || []).filter(e => e.season === season).sort((a, b) => a.week - b.week);
  if (seasonEntries.length === 0) return "";

  const rankWeek = (entry) => Object.entries(entry.teams)
    .filter(([, t]) => t.power_score != null)
    .map(([tid, t]) => ({ tid, score: t.power_score }))
    .sort((a, b) => b.score - a.score)
    .map((t, i) => ({ ...t, rank: i + 1 }));

  const latest = seasonEntries[seasonEntries.length - 1];
  const latestRanked = rankWeek(latest);
  if (latestRanked.length === 0) return "";

  let prevRankByTid = null;
  if (seasonEntries.length > 1) {
    prevRankByTid = {};
    rankWeek(seasonEntries[seasonEntries.length - 2]).forEach(t => { prevRankByTid[t.tid] = t.rank; });
  }

  // roster_id -> manager_id, via team_name, so each row can look up its
  // streak/luck/positional_ranks from season_plots.json's teams dict
  // (keyed by manager_id, unlike power_log.json which is roster_id-keyed).
  const managerIdByTeamName = {};
  Object.entries(plotTeams || {}).forEach(([mgrId, t]) => { managerIdByTeamName[t.team_name] = mgrId; });
  const numTeams = latestRanked.length;

  // Bench points ranked across the whole league for this week, so the
  // "left N points on the bench" candidate only fires for the top 2 --
  // otherwise it'd fire for literally everyone (every team has SOME
  // bench points) and stop being a notable callout at all.
  const benchRankByMgrId = {};
  Object.entries(plotTeams || {})
    .filter(([, t]) => t.last_week_bench_points != null)
    .sort((a, b) => b[1].last_week_bench_points - a[1].last_week_bench_points)
    .forEach(([mgrId], i) => { benchRankByMgrId[mgrId] = i + 1; });

  const rows = latestRanked.map(t => {
    const teamName = (teamNameByRosterId || {})[t.tid] || `Roster ${t.tid}`;
    const avatar = avatarImg((avatarByTeamName || {})[teamName], teamName);

    let moveHtml = "", moveDirection = null, moveAmount = 0;
    if (prevRankByTid && prevRankByTid[t.tid] != null) {
      const prevRank = prevRankByTid[t.tid];
      if (t.rank < prevRank) { moveDirection = "up"; moveAmount = prevRank - t.rank; moveHtml = `<span class="rank-move rank-up" title="Up from #${prevRank}">&#9650; ${moveAmount}</span>`; }
      else if (t.rank > prevRank) { moveDirection = "down"; moveAmount = t.rank - prevRank; moveHtml = `<span class="rank-move rank-down" title="Down from #${prevRank}">&#9660; ${moveAmount}</span>`; }
      else moveHtml = `<span class="rank-move rank-same" title="Unchanged">&#8213;</span>`;
    }

    const mgrId = managerIdByTeamName[teamName];
    const teamData = mgrId ? plotTeams[mgrId] : null;
    const benchRank = mgrId ? benchRankByMgrId[mgrId] : null;
    const blurb = renderPowerRankBlurb(teamName, teamData, numTeams, moveDirection, moveAmount, benchRank, `${season}-${latest.week}-${t.tid}-blurb`);

    return `
      <div class="power-rank-row">
        <div class="power-rank-row-main">
          <span class="power-rank-number">${t.rank}</span>
          ${avatar}
          <span class="power-rank-name">${escapeHtml(teamName)}</span>
          <span class="power-rank-score">${t.score.toFixed(1)}</span>
          ${moveHtml}
        </div>
        <p class="power-rank-blurb">${escapeHtml(blurb)}</p>
      </div>`;
  }).join("");

  return `<div class="power-rank-list">${rows}</div>`;
}

function chartCard(kicker, title, bodyHtml, wide) {
  return `
    <div class="card chart-card${wide ? " chart-card-wide" : ""}">
      <p class="placeholder-kicker">${escapeHtml(kicker)}</p>
      <h3 class="chart-title">${escapeHtml(title)}</h3>
      ${bodyHtml}
    </div>`;
}

async function loadSeasonPlots(teamNameByRosterId, avatarByTeamName) {
  const container = document.getElementById("season-plots-content");
  try {
    const [plotsRes, logRes] = await Promise.all([
      fetch("data/season_plots.json"),
      fetch("data/power_log.json"),
    ]);
    if (!plotsRes.ok) throw new Error("No season plot data generated yet.");
    const plots = await plotsRes.json();
    const log = logRes.ok ? await logRes.json() : { entries: [] };

    container.innerHTML = `
      <div class="season-plots-grid">
        ${chartCard("LADY LUCK", "Actual Wins vs. Expected Wins", renderLadyLuck(plots.teams, avatarByTeamName), true)}
        ${chartCard("POWER RANKINGS", "Where Things Stand",
          renderCurrentPowerRankings(log.entries || [], plots.season, teamNameByRosterId, avatarByTeamName, plots.teams) +
          renderPowerRankHistory(log.entries || [], plots.season, teamNameByRosterId, avatarByTeamName), true)}
        ${chartCard("POSITIONAL PPW", "Points Per Week by Slot", renderPositionalPpw(plots.teams))}
        ${chartCard("START FLEXIN'", "Flex Slot Usage League-Wide", renderFlexDistribution(plots.flex_distribution || {}))}
        ${chartCard("WHAT-IF SCHEDULE", "Record vs. a Neutral Schedule", renderWhatIfSchedule(plots.teams, plots.weeks_complete))}
        ${chartCard("WHAT-IF SCHEDULE GRID", "Every Manager vs. Every Other Manager's Schedule", renderWhatIfGrid(plots.what_if_grid), true)}
      </div>`;

    wirePowerRankHover(container);
  } catch (err) {
    container.innerHTML = `<p class="loading-msg">${escapeHtml(err.message)}</p>`;
  }
}
