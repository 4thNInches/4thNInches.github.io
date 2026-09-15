// ============================================================
// recaps.html client logic
// script.js (loaded before this on the page) handles the shared header
// ticker via loadFeed() -- everything here is specific to this page:
// figuring out which week to show, loading data/previews/<season>-wk<week>.json,
// and prev/next navigation via the URL's ?season=&week= params.
// ============================================================

const RECAPS_SLEEPER_LEAGUE_ID = "1392229432336347136"; // NOT named SLEEPER_LEAGUE_ID on purpose --
// script.js (loaded before this on the page) already declares that exact name as a top-level const,
// and browser <script> tags share one global scope, unlike the Python scripts' separate processes.
// Redeclaring it here threw a SyntaxError that silently killed this entire file before anything in
// it could run -- caught from a real bug report, not a guess.
const SLEEPER_API = "https://api.sleeper.app/v1";

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
  try {
    const [users, rosters] = await Promise.all([
      fetch(`${SLEEPER_API}/league/${RECAPS_SLEEPER_LEAGUE_ID}/users`).then(r => r.json()),
      fetch(`${SLEEPER_API}/league/${RECAPS_SLEEPER_LEAGUE_ID}/rosters`).then(r => r.json()),
    ]);
    const userById = {};
    users.forEach(u => { userById[u.user_id] = u; });

    const lookup = {};
    rosters.forEach(r => {
      const u = userById[r.owner_id];
      if (!u) return;
      const teamName = (u.metadata && u.metadata.team_name) || u.display_name;
      const avatarId = (u.metadata && u.metadata.avatar) || u.avatar || null;
      if (teamName) lookup[teamName] = avatarId;
    });
    return lookup;
  } catch {
    return {}; // avatarImg() already renders a graceful empty placeholder for a missing id
  }
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
    list.innerHTML = data.matchups.map(m => renderMatchup(m, avatarLookup)).join("");
  } catch (err) {
    list.innerHTML = `<p class="loading-msg">${escapeHtml(err.message)}</p>`;
  }
}

async function init() {
  let { season, week } = getWeekFromUrl();
  const avatarLookupPromise = buildAvatarLookup(); // kick off in parallel, don't block week detection on it

  if (season === null || week === null) {
    const defaults = await detectDefaultSeasonWeek();
    season = season ?? defaults.season;
    week = week ?? defaults.week;
  }
  setWeekInUrl(season, week);

  const avatarLookup = await avatarLookupPromise;
  loadPreviews(season, week, avatarLookup);

  document.getElementById("prev-week-btn").addEventListener("click", () => {
    if (week <= 1) return;
    week -= 1;
    setWeekInUrl(season, week);
    loadPreviews(season, week, avatarLookup);
  });

  document.getElementById("next-week-btn").addEventListener("click", () => {
    week += 1;
    setWeekInUrl(season, week);
    loadPreviews(season, week, avatarLookup);
  });
}

document.addEventListener("DOMContentLoaded", init);
