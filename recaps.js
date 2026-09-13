// ============================================================
// recaps.html client logic
// script.js (loaded before this on the page) handles the shared header
// ticker via loadFeed() -- everything here is specific to this page:
// figuring out which week to show, loading data/previews/<season>-wk<week>.json,
// and prev/next navigation via the URL's ?season=&week= params.
// ============================================================

const SLEEPER_LEAGUE_ID = "1392229432336347136"; // same convention as the Python scripts -- redeclared per-file, not shared
const SLEEPER_API = "https://api.sleeper.app/v1";

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

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
      fetch(`${SLEEPER_API}/league/${SLEEPER_LEAGUE_ID}`).then(r => r.json()),
      fetch(`${SLEEPER_API}/state/nfl`).then(r => r.json()),
    ]);
    const week = state.season_type === "pre" ? 1 : state.week;
    return { season: league.season, week };
  } catch {
    return { season: new Date().getFullYear(), week: 1 };
  }
}

function renderMatchup(m) {
  return `
    <div class="card preview-card">
      <p class="preview-teams">${escapeHtml(m.team_a)} <span class="preview-vs">vs</span> ${escapeHtml(m.team_b)}</p>
      <p class="preview-text">${escapeHtml(m.preview)}</p>
    </div>`;
}

async function loadPreviews(season, week) {
  const heading = document.getElementById("week-heading");
  const list = document.getElementById("preview-list");
  heading.textContent = `Week ${week} Previews`;
  list.innerHTML = `<p class="loading-msg">Loading previews&hellip;</p>`;

  try {
    const res = await fetch(`data/previews/${season}-wk${week}.json`);
    if (!res.ok) throw new Error(`No preview generated for Week ${week} yet`);
    const data = await res.json();
    if (!data.matchups || data.matchups.length === 0) {
      list.innerHTML = `<p class="loading-msg">No matchups found for Week ${week}.</p>`;
      return;
    }
    list.innerHTML = data.matchups.map(renderMatchup).join("");
  } catch (err) {
    list.innerHTML = `<p class="loading-msg">${escapeHtml(err.message)}</p>`;
  }
}

async function init() {
  let { season, week } = getWeekFromUrl();
  if (season === null || week === null) {
    const defaults = await detectDefaultSeasonWeek();
    season = season ?? defaults.season;
    week = week ?? defaults.week;
  }
  setWeekInUrl(season, week);
  loadPreviews(season, week);

  document.getElementById("prev-week-btn").addEventListener("click", () => {
    if (week <= 1) return;
    week -= 1;
    setWeekInUrl(season, week);
    loadPreviews(season, week);
  });

  document.getElementById("next-week-btn").addEventListener("click", () => {
    week += 1;
    setWeekInUrl(season, week);
    loadPreviews(season, week);
  });
}

document.addEventListener("DOMContentLoaded", init);
