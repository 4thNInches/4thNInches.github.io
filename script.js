// 4th & Inches — site script

const SLEEPER_LEAGUE_ID = "1392229432336347136";

const PLACE_ICON = { first: "\u{1F3C6}", second: "\u{1F948}", third: "\u{1F949}" };

let standingsRows = [];
let standingsSortKey = "rank";
let standingsSortDir = -1; // -1 = descending, 1 = ascending

const STANDINGS_COLUMNS = [
  { key: "rank", label: "#" },
  { key: "teamName", label: "Team" },
  { key: "wins", label: "Record" }, // "sort by record" = by total wins, matching lifetime table
  { key: "pointsFor", label: "PF" },
  { key: "pfPerWk", label: "PF/Wk" },
  { key: "pointsAgainst", label: "PA" },
  { key: "paPerWk", label: "PA/Wk" },
  { key: "streakSort", label: "Streak" },
  { key: "playoffPct", label: "Playoff %" },
  { key: "byePct", label: "Bye %" },
];

let sharedPlayedWeeksPromise = null;
function getSharedPlayedWeeks() {
  if (!sharedPlayedWeeksPromise) {
    sharedPlayedWeeksPromise = (async () => {
      const regSeasonWeeks = await fetchRegularSeasonWeekCount(SLEEPER_LEAGUE_ID);
      return fetchPlayedSleeperWeeks(SLEEPER_LEAGUE_ID, regSeasonWeeks);
    })();
  }
  return sharedPlayedWeeksPromise;
}

function computeRosterSequences(playedWeeks) {
  const sequences = {}; // roster_id -> ["W"/"L"/"T", ...] in chronological order
  playedWeeks.forEach(({ matchups }) => {
    const byMatchupId = {};
    matchups.forEach(m => {
      if (m.matchup_id === null || m.matchup_id === undefined) return; // bye -- not a real matchup
      (byMatchupId[m.matchup_id] = byMatchupId[m.matchup_id] || []).push(m);
    });
    Object.values(byMatchupId).forEach(pair => {
      if (pair.length !== 2) return;
      const [a, b] = pair;
      const scoreA = a.points || 0;
      const scoreB = b.points || 0;
      (sequences[a.roster_id] = sequences[a.roster_id] || []).push(scoreA > scoreB ? "W" : scoreA < scoreB ? "L" : "T");
      (sequences[b.roster_id] = sequences[b.roster_id] || []).push(scoreB > scoreA ? "W" : scoreB < scoreA ? "L" : "T");
    });
  });
  return sequences;
}

async function loadStandings(leagueId) {
  const container = document.getElementById("standings-table");
  if (!container) return;

  let rosters, users, sleeperMapping, managers;
  try {
    [rosters, users, sleeperMapping, managers] = await Promise.all([
      fetch(`https://api.sleeper.app/v1/league/${leagueId}/rosters`).then(r => {
        if (!r.ok) throw new Error(`rosters HTTP ${r.status}`);
        return r.json();
      }),
      fetch(`https://api.sleeper.app/v1/league/${leagueId}/users`).then(r => {
        if (!r.ok) throw new Error(`users HTTP ${r.status}`);
        return r.json();
      }),
      fetch("data/sleeper_manager_mapping.json").then(r => (r.ok ? r.json() : {})).catch(() => ({})),
      fetch("data/managers.json").then(r => (r.ok ? r.json() : {})).catch(() => ({})),
    ]);
  } catch (err) {
    container.innerHTML = `<p class="loading-msg">Couldn't load standings from Sleeper (${err.message}). Sleeper's API is public and needs no auth, so this is usually a temporary network issue — try refreshing.</p>`;
    return;
  }

  const usersById = {};
  users.forEach(u => { usersById[u.user_id] = u; });

  standingsRows = rosters.map(r => {
    const user = usersById[r.owner_id] || {};
    const teamName = (user.metadata && user.metadata.team_name) || user.display_name || "Unclaimed team";
    const mgrId = sleeperMapping[user.display_name];
    const managerName = (mgrId && managers[mgrId] && managers[mgrId].display_name) || user.display_name || "\u2014";
    const avatarId = (user.metadata && user.metadata.avatar) || user.avatar || null;
    const settings = r.settings || {};
    const wins = settings.wins || 0;
    const losses = settings.losses || 0;
    const ties = settings.ties || 0;
    const played = wins + losses + ties;
    const pointsFor = pointsFromSettings(settings.fpts, settings.fpts_decimal);
    const pointsAgainst = pointsFromSettings(settings.fpts_against, settings.fpts_against_decimal);
    return {
      rosterId: r.roster_id,
      teamName,
      managerName,
      avatarId,
      wins, losses, ties,
      pointsFor,
      pointsAgainst,
      pfPerWk: played ? pointsFor / played : 0,
      paPerWk: played ? pointsAgainst / played : 0,
      streak: { count: 0, type: null },
      streakSort: 0,
      playoffPct: "\u2014",
      byePct: "\u2014",
    };
  });

  renderStandingsTable(); // render immediately; streak fills in once weekly data resolves

  try {
    const playedWeeks = await getSharedPlayedWeeks();
    const sequences = computeRosterSequences(playedWeeks);
    standingsRows = standingsRows.map(row => {
      const streak = extendStreak(null, sequences[row.rosterId] || []);
      return { ...row, streak, streakSort: streakSortValue(streak) };
    });
    renderStandingsTable();
  } catch (err) {
    console.warn("Couldn't compute current-season streaks:", err);
  }
}

function renderStandingsTable() {
  const container = document.getElementById("standings-table");
  if (!container) return;

  const dir = standingsSortDir;
  const key = standingsSortKey;

  let sorted;
  if (key === "rank") {
    // default order: wins desc, then PF desc as tiebreak
    sorted = [...standingsRows].sort((a, b) => (b.wins - a.wins) || (b.pointsFor - a.pointsFor));
    if (dir === 1) sorted.reverse();
  } else {
    sorted = [...standingsRows].sort((a, b) => {
      let av = a[key], bv = b[key];
      if (typeof av === "string") { av = av.toLowerCase(); bv = (bv || "").toLowerCase(); }
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }

  const allZero = standingsRows.every(row => row.wins === 0 && row.losses === 0 && row.ties === 0);

  const headerCells = STANDINGS_COLUMNS.map(col => {
    const active = col.key === key;
    const arrow = active ? (dir === -1 ? " \u25BC" : " \u25B2") : "";
    return `<th scope="col" data-sort-key="${col.key}" class="${active ? "standings-sorted" : ""}">${col.label}${arrow}</th>`;
  }).join("");

  const bodyRows = sorted.map((row, i) => `
    <tr>
      <td class="standings-rank">${i + 1}</td>
      <td>
        <span class="standings-team-cell">
          ${avatarImg(row.avatarId, row.teamName)}
          <span class="standings-team-text">
            <span class="standings-team">${escapeHtml(row.teamName)}</span>
            <span class="standings-manager">${escapeHtml(row.managerName)}</span>
          </span>
        </span>
      </td>
      <td class="standings-record">${row.wins}-${row.losses}${row.ties ? `-${row.ties}` : ""}</td>
      <td class="standings-pts">${row.pointsFor.toFixed(1)}</td>
      <td class="standings-pts">${row.pfPerWk.toFixed(2)}</td>
      <td class="standings-pts">${row.pointsAgainst.toFixed(1)}</td>
      <td class="standings-pts">${row.paPerWk.toFixed(2)}</td>
      <td class="standings-pts streak-${(row.streak && row.streak.type) || "none"}">${formatStreak(row.streak)}</td>
      <td class="standings-pts">${row.playoffPct}</td>
      <td class="standings-pts">${row.byePct}</td>
    </tr>
  `).join("");

  container.innerHTML = `
    ${allZero ? `<p class="loading-msg standings-note">Preseason \u2014 records are 0-0 until Week 1 kicks off.</p>` : ""}
    <table class="standings-real-table">
      <thead><tr>${headerCells}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
  `;

  container.querySelectorAll("th[data-sort-key]").forEach(th => {
    th.addEventListener("click", () => {
      const clickedKey = th.getAttribute("data-sort-key");
      if (clickedKey === standingsSortKey) {
        standingsSortDir *= -1;
      } else {
        standingsSortKey = clickedKey;
        standingsSortDir = clickedKey === "rank" ? -1 : -1;
      }
      renderStandingsTable();
    });
  });
}


function avatarImg(avatarId, teamName) {
  if (!avatarId) {
    return `<span class="standings-avatar standings-avatar-empty" aria-hidden="true"></span>`;
  }
  const url = `https://sleepercdn.com/avatars/thumbs/${avatarId}`;
  return `<img class="standings-avatar" src="${url}" alt="" loading="lazy" onerror="this.classList.add('standings-avatar-empty'); this.removeAttribute('src');">`;
}

function pointsFromSettings(whole, decimal) {
  const w = whole || 0;
  const d = decimal || 0;
  return w + d / 100;
}

async function loadChampions() {
  const container = document.getElementById("champs-table");
  if (!container) return;

  let seasons;
  try {
    const res = await fetch("data/champions.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    seasons = await res.json();
  } catch (err) {
    container.innerHTML = `<p class="loading-msg">Couldn't load championship history (${err.message}). If you're viewing this file directly from disk, run a local server instead — see the README.</p>`;
    return;
  }

  renderChampsTable(container, seasons);
}

const TICKER_ROTATE_MS = 10000;
const TICKER_FADE_MS = 500;
const TICKER_ROTATE_SECONDS = Math.round(TICKER_ROTATE_MS / 1000);

let playClockInterval = null;

function resetPlayClock() {
  const clock = document.getElementById("play-clock");
  const digit = document.getElementById("play-clock-digit");
  const bar = document.getElementById("play-clock-bar");
  if (!clock || !digit || !bar) return;

  clock.classList.remove("is-kicking");
  clearInterval(playClockInterval);

  let remaining = TICKER_ROTATE_SECONDS;
  digit.textContent = remaining;

  // Restart the drain bar: snap it back to full with no transition, force a
  // reflow so the browser registers that reset, then re-enable the
  // transition and animate to empty over the full interval in one go.
  bar.style.transition = "none";
  bar.style.transform = "scaleX(1)";
  void bar.offsetWidth;
  bar.style.transition = `transform ${TICKER_ROTATE_SECONDS}s linear`;
  bar.style.transform = "scaleX(0)";

  playClockInterval = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(playClockInterval);
      digit.textContent = "0";
      clock.classList.add("is-kicking");
    } else {
      digit.textContent = remaining;
    }
  }, 1000);
}

async function loadTicker() {
  const el = document.getElementById("ticker-text");
  if (!el) return;

  let facts;
  try {
    const res = await fetch("data/facts.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    facts = await res.json();
  } catch (err) {
    el.textContent = `Couldn't load league facts (${err.message}).`;
    return;
  }

  if (!Array.isArray(facts) || facts.length === 0) {
    el.textContent = "No league facts yet.";
    return;
  }

  const byCategory = {};
  facts.forEach(f => {
    (byCategory[f.category] = byCategory[f.category] || []).push(f);
  });
  const categories = Object.keys(byCategory);

  let lastId = null;

  function pickFact() {
    // category first, then a fact within it -- keeps rare categories from
    // getting drowned out by ones with hundreds of entries (e.g. head-to-head)
    for (let attempt = 0; attempt < 8; attempt++) {
      const cat = categories[Math.floor(Math.random() * categories.length)];
      const pool = byCategory[cat];
      const fact = pool[Math.floor(Math.random() * pool.length)];
      if (fact.id !== lastId || facts.length === 1) return fact;
    }
    return facts[Math.floor(Math.random() * facts.length)];
  }

  function showFact() {
    const fact = pickFact();
    lastId = fact.id;
    el.classList.add("is-swapping");
    setTimeout(() => {
      el.textContent = fact.text;
      el.classList.remove("is-swapping");
    }, TICKER_FADE_MS);
    resetPlayClock();
  }

  showFact();
  setInterval(showFact, TICKER_ROTATE_MS);
}

function renderChampsTable(container, seasons) {
  const rows = seasons.map(s => `
    <tr>
      <td class="champs-season">${s.season}</td>
      ${["first", "second", "third"].map(place => `
        <td class="champs-cell">
          <span class="champs-place-icon" aria-hidden="true">${PLACE_ICON[place]}</span>
          <span class="champs-team">${escapeHtml(s[place].team)}</span>
          <span class="champs-manager">${escapeHtml(s[place].manager)}</span>
        </td>
      `).join("")}
    </tr>
  `).join("");

  container.innerHTML = `
    <table class="champs-real-table">
      <thead>
        <tr>
          <th scope="col">Season</th>
          <th scope="col">${PLACE_ICON.first} 1st</th>
          <th scope="col">${PLACE_ICON.second} 2nd</th>
          <th scope="col">${PLACE_ICON.third} 3rd</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function loadStatCards() {
  const ids = {
    highest_scores: "stat-highest-scores",
    closest_margins: "stat-closest-margins",
    longest_streaks: "stat-longest-streaks",
    most_championships: "stat-most-championships",
  };

  if (!Object.values(ids).some(id => document.getElementById(id))) return;

  let data;
  try {
    const res = await fetch("data/stat_cards.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    Object.values(ids).forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<li class="stat-list-loading">Couldn't load stats (${escapeHtml(err.message)}).</li>`;
    });
    return;
  }

  renderTextStatList(ids.highest_scores, data.highest_scores);
  renderTextStatList(ids.closest_margins, data.closest_margins);
  renderTextStatList(ids.longest_streaks, data.longest_streaks);
  renderTextStatList(ids.most_championships, data.most_championships);
}

function renderTextStatList(elId, entries) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!Array.isArray(entries) || entries.length === 0) {
    el.innerHTML = `<li class="stat-list-loading">No data yet.</li>`;
    return;
  }
  el.innerHTML = entries.map(e => {
    if (e.headline) {
      return `<li><strong class="stat-headline">${escapeHtml(e.headline)}</strong> ${escapeHtml(e.detail || "")}</li>`;
    }
    return `<li>${escapeHtml(e.text)}</li>`;
  }).join("");
}

let lifetimeRows = [];
let lifetimeSortKey = "win_pct";
let lifetimeSortDir = -1; // -1 = descending, 1 = ascending
let lifetimeShowActiveOnly = false;

const LIFETIME_COLUMNS = [
  { key: "display", label: "Team (Manager)", numeric: false },
  { key: "seasons", label: "Seasons", numeric: true },
  { key: "wins", label: "Record", numeric: true }, // "sort by record" = by total wins
  { key: "win_pct", label: "W%", numeric: true },
  { key: "pf", label: "PF", numeric: true },
  { key: "pf_per_wk", label: "PF/Wk", numeric: true },
  { key: "pa", label: "PA", numeric: true },
  { key: "pa_per_wk", label: "PA/Wk", numeric: true },
  { key: "streak_sort", label: "Streak", numeric: true },
  { key: "playoff_seasons", label: "Playoff Szns", numeric: true },
  { key: "playoff_wins", label: "Playoff Rec", numeric: true },
  { key: "trophy_case", label: "Trophy Case", numeric: false },
];

const LIFETIME_CACHE_KEY = "4thinches_lifetime_live_v1";
const LIFETIME_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes -- fresh enough to feel "live", short enough to spare Sleeper repeat hits

function readLifetimeCache() {
  try {
    const raw = sessionStorage.getItem(LIFETIME_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed.timestamp || Date.now() - parsed.timestamp > LIFETIME_CACHE_TTL_MS) return null;
    return parsed.data;
  } catch {
    return null; // sessionStorage unavailable (private browsing, quota, etc.) -- just skip caching
  }
}

function writeLifetimeCache(data) {
  try {
    sessionStorage.setItem(LIFETIME_CACHE_KEY, JSON.stringify({ timestamp: Date.now(), data }));
  } catch {
    // fine to fail silently -- caching is an optimization, not a requirement
  }
}

async function loadLifetimeStandings() {
  const container = document.getElementById("lifetime-table");
  if (!container) return;

  let baseline;
  try {
    const res = await fetch("data/lifetime_standings.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    baseline = await res.json();
  } catch (err) {
    container.innerHTML = `<p class="loading-msg">Couldn't load lifetime standings (${escapeHtml(err.message)}).</p>`;
    return;
  }

  if (!Array.isArray(baseline) || baseline.length === 0) {
    container.innerHTML = `<p class="loading-msg">No data yet.</p>`;
    return;
  }

  lifetimeRows = baseline.map(prepareLifetimeRow);
  setupLifetimeToggle();
  renderLifetimeTable(); // show historical baseline immediately, don't block on live fetch

  const cached = readLifetimeCache();
  if (cached) {
    lifetimeRows = cached.map(prepareLifetimeRow);
    renderLifetimeTable();
    return;
  }

  try {
    const merged = await mergeLiveSleeperSeason(baseline);
    writeLifetimeCache(merged);
    lifetimeRows = merged.map(prepareLifetimeRow);
    renderLifetimeTable();
  } catch (err) {
    console.warn("Couldn't merge live Sleeper season into lifetime standings:", err);
    // Baseline is already rendered -- fail quietly rather than blocking the table.
  }
}


function setupLifetimeToggle() {
  const toggle = document.getElementById("lifetime-active-toggle");
  if (!toggle || toggle.dataset.wired) return;
  toggle.dataset.wired = "true";
  toggle.checked = lifetimeShowActiveOnly;
  toggle.addEventListener("change", () => {
    lifetimeShowActiveOnly = toggle.checked;
    renderLifetimeTable();
  });
}

function prepareLifetimeRow(r) {
  return { ...r, streak_sort: streakSortValue(r.current_streak) };
}

function streakSortValue(streak) {
  if (!streak || !streak.type) return 0;
  return streak.type === "W" ? streak.count : -streak.count;
}

function formatStreak(streak) {
  if (!streak || !streak.type || !streak.count) return "\u2014";
  return `${streak.type}${streak.count}`;
}

function formatWinPct(p) {
  const s = p.toFixed(3);
  return p < 1 ? s.replace(/^0/, "") : s;
}

async function mergeLiveSleeperSeason(baseline) {
  const sleeperMapping = await fetch("data/sleeper_manager_mapping.json").then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status} loading sleeper_manager_mapping.json`);
    return r.json();
  });

  const [rosters, users] = await Promise.all([
    fetch(`https://api.sleeper.app/v1/league/${SLEEPER_LEAGUE_ID}/rosters`).then(r => r.json()),
    fetch(`https://api.sleeper.app/v1/league/${SLEEPER_LEAGUE_ID}/users`).then(r => r.json()),
  ]);

  const userById = {};
  users.forEach(u => { userById[u.user_id] = u; });

  const rosterToManager = {};
  const rosterToTeamName = {};
  const rosterToAvatarId = {};
  rosters.forEach(r => {
    const user = userById[r.owner_id];
    if (!user) return;
    const teamName = (user.metadata && user.metadata.team_name) || user.display_name;
    rosterToTeamName[r.roster_id] = teamName;
    rosterToAvatarId[r.roster_id] = (user.metadata && user.metadata.avatar) || user.avatar || null;
    const mgr = sleeperMapping[user.display_name];
    if (mgr && mgr !== "FILL_IN_MANAGER_ID") {
      rosterToManager[r.roster_id] = mgr;
    }
  });

  const playedWeeks = await getSharedPlayedWeeks();
  const live = computeLiveStatsFromWeeks(playedWeeks, rosterToManager);

  const byManager = {};
  baseline.forEach(r => { byManager[r.manager_id] = r; });

  // Managers with live games this season but no Yahoo history at all yet
  // (brand new for 2026) get a synthetic zero baseline.
  Object.keys(live).forEach(mgr => {
    if (!byManager[mgr]) {
      const rosterEntry = Object.entries(rosterToManager).find(([, m]) => m === mgr);
      const teamName = rosterEntry ? (rosterToTeamName[rosterEntry[0]] || mgr) : mgr;
      byManager[mgr] = {
        manager_id: mgr, manager: mgr, team_name: teamName, display: teamName,
        active: true, seasons: 0, wins: 0, losses: 0, ties: 0, record: "0-0",
        win_pct: 0, pf: 0, pa: 0, pf_per_wk: 0, pa_per_wk: 0,
        current_streak: { count: 0, type: null },
        playoff_seasons: 0, playoff_wins: 0, playoff_losses: 0,
        playoff_record: "0-0", trophy_case: "",
      };
    }
  });

  return Object.values(byManager).map(row => mergeRow(row, live[row.manager_id], rosterToTeamName, rosterToManager, rosterToAvatarId));
}

async function fetchRegularSeasonWeekCount(leagueId, fallback = 14) {
  try {
    const res = await fetch(`https://api.sleeper.app/v1/league/${leagueId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const league = await res.json();
    const playoffStart = league.settings && league.settings.playoff_week_start;
    if (playoffStart && playoffStart > 1) return playoffStart - 1;
  } catch (err) {
    console.warn(`Couldn't fetch Sleeper playoff_week_start, falling back to week ${fallback}:`, err);
  }
  return fallback;
}

async function fetchPlayedSleeperWeeks(leagueId, maxWeeks = 18) {
  const weeks = [];
  for (let week = 1; week <= maxWeeks; week++) {
    let data;
    try {
      const res = await fetch(`https://api.sleeper.app/v1/league/${leagueId}/matchups/${week}`);
      if (!res.ok) break;
      data = await res.json();
    } catch {
      break;
    }
    if (!Array.isArray(data) || data.length === 0) break;
    const anyScored = data.some(m => (m.points || 0) > 0);
    if (!anyScored) break; // this week hasn't been played yet -- stop here
    weeks.push({ week, matchups: data });
  }
  return weeks;
}

function computeLiveStatsFromWeeks(playedWeeks, rosterToManager) {
  const live = {};
  const ensure = mgr => (live[mgr] = live[mgr] || { wins: 0, losses: 0, ties: 0, pf: 0, pa: 0, sequence: [] });

  playedWeeks.forEach(({ matchups }) => {
    const byMatchupId = {};
    matchups.forEach(m => {
      if (m.matchup_id === null || m.matchup_id === undefined) return; // bye -- not a real matchup
      (byMatchupId[m.matchup_id] = byMatchupId[m.matchup_id] || []).push(m);
    });

    Object.values(byMatchupId).forEach(pair => {
      if (pair.length !== 2) return; // bye or malformed entry
      const [a, b] = pair;
      const scoreA = a.points || 0;
      const scoreB = b.points || 0;
      recordLiveResult(ensure, rosterToManager[a.roster_id], scoreA, scoreB);
      recordLiveResult(ensure, rosterToManager[b.roster_id], scoreB, scoreA);
    });
  });

  return live;
}

function recordLiveResult(ensure, mgr, myScore, oppScore) {
  if (!mgr) return;
  const s = ensure(mgr);
  s.pf += myScore;
  s.pa += oppScore;
  if (myScore > oppScore) { s.wins++; s.sequence.push("W"); }
  else if (myScore < oppScore) { s.losses++; s.sequence.push("L"); }
  else { s.ties++; s.sequence.push("T"); }
}

function extendStreak(baselineStreak, sequence) {
  let type = (baselineStreak && baselineStreak.type) || null;
  let count = (baselineStreak && baselineStreak.count) || 0;
  sequence.forEach(result => {
    if (result === "T") { type = null; count = 0; return; }
    if (result === type) count += 1;
    else { type = result; count = 1; }
  });
  return { count, type };
}

function mergeRow(baseRow, liveStats, rosterToTeamName, rosterToManager, rosterToAvatarId) {
  const l = liveStats || { wins: 0, losses: 0, ties: 0, pf: 0, pa: 0, sequence: [] };

  const wins = (baseRow.wins || 0) + l.wins;
  const losses = (baseRow.losses || 0) + l.losses;
  const ties = (baseRow.ties || 0) + l.ties;
  const played = wins + losses + ties;
  const winPct = played ? (wins + 0.5 * ties) / played : 0;
  const pf = (baseRow.pf || 0) + l.pf;
  const pa = (baseRow.pa || 0) + l.pa;

  const streak = extendStreak(baseRow.current_streak, l.sequence);

  // Prefer the live Sleeper team name/avatar if this manager currently owns
  // a roster; otherwise keep whatever the baseline already resolved.
  let teamName = baseRow.team_name;
  let avatarId = baseRow.avatarId || null;
  for (const [rosterId, mgrId] of Object.entries(rosterToManager)) {
    if (mgrId === baseRow.manager_id) {
      if (rosterToTeamName[rosterId]) teamName = rosterToTeamName[rosterId];
      if (rosterToAvatarId[rosterId]) avatarId = rosterToAvatarId[rosterId];
    }
  }

  return {
    ...baseRow,
    team_name: teamName,
    avatarId,
    display: `${teamName} (${baseRow.manager})`,
    wins, losses, ties,
    record: `${wins}-${losses}` + (ties ? `-${ties}` : ""),
    win_pct: winPct,
    win_pct_display: formatWinPct(winPct),
    pf: Math.round(pf * 100) / 100,
    pa: Math.round(pa * 100) / 100,
    pf_per_wk: played ? Math.round((pf / played) * 100) / 100 : 0,
    pa_per_wk: played ? Math.round((pa / played) * 100) / 100 : 0,
    current_streak: streak,
    playoff_wins: baseRow.playoff_wins || 0,
    playoff_losses: baseRow.playoff_losses || 0,
  };
}

function renderLifetimeTable() {
  const container = document.getElementById("lifetime-table");
  if (!container) return;

  const dir = lifetimeSortDir;
  const key = lifetimeSortKey;
  const filtered = lifetimeShowActiveOnly ? lifetimeRows.filter(r => r.active) : lifetimeRows;
  const sorted = [...filtered].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === "string") { av = av.toLowerCase(); bv = (bv || "").toLowerCase(); }
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });

  const headerCells = LIFETIME_COLUMNS.map(col => {
    const active = col.key === key;
    const arrow = active ? (dir === -1 ? " \u25BC" : " \u25B2") : "";
    return `<th scope="col" data-sort-key="${col.key}" class="${active ? "lifetime-sorted" : ""}">${col.label}${arrow}</th>`;
  }).join("");

  const bodyRows = sorted.map(r => `
    <tr class="${r.active ? "" : "lifetime-inactive"}">
      <td>
        <span class="standings-team-cell">
          ${avatarImg(r.avatarId, r.team_name)}
          <span class="standings-team-text">
            <span class="standings-team">${escapeHtml(r.team_name)}</span>
            <span class="standings-manager">${escapeHtml(r.manager)}</span>
          </span>
        </span>
      </td>
      <td class="lifetime-numeric">${r.seasons}</td>
      <td class="lifetime-numeric">${escapeHtml(r.record)}</td>
      <td class="lifetime-numeric">${escapeHtml(r.win_pct_display)}</td>
      <td class="lifetime-numeric">${r.pf.toFixed(2)}</td>
      <td class="lifetime-numeric">${r.pf_per_wk.toFixed(2)}</td>
      <td class="lifetime-numeric">${r.pa.toFixed(2)}</td>
      <td class="lifetime-numeric">${r.pa_per_wk.toFixed(2)}</td>
      <td class="lifetime-numeric streak-${(r.current_streak && r.current_streak.type) || "none"}">${formatStreak(r.current_streak)}</td>
      <td class="lifetime-numeric">${r.playoff_seasons}</td>
      <td class="lifetime-numeric">${escapeHtml(r.playoff_record)}</td>
      <td class="lifetime-trophy">${r.trophy_case || "\u2014"}</td>
    </tr>
  `).join("");

  container.innerHTML = `
    <table class="lifetime-real-table">
      <thead><tr>${headerCells}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
  `;

  container.querySelectorAll("th[data-sort-key]").forEach(th => {
    th.addEventListener("click", () => {
      const clickedKey = th.getAttribute("data-sort-key");
      if (clickedKey === lifetimeSortKey) {
        lifetimeSortDir *= -1;
      } else {
        lifetimeSortKey = clickedKey;
        lifetimeSortDir = -1;
      }
      renderLifetimeTable();
    });
  });
}


document.addEventListener("DOMContentLoaded", () => {
  loadStandings(SLEEPER_LEAGUE_ID);
  loadChampions();
  loadTicker();
  loadStatCards();
  loadLifetimeStandings();
});

// Below: nothing live yet for weekly stats or the feed. This is the spot
// where those get fetched and dropped into the remaining placeholder cards
// in index.html (#stats cards, #feed).
//
// Sleeper's API is public and read-only, no auth needed:
//   https://api.sleeper.app/v1/league/<league_id>
//   https://api.sleeper.app/v1/league/<league_id>/rosters
//   https://api.sleeper.app/v1/league/<league_id>/users
//   https://api.sleeper.app/v1/league/<league_id>/matchups/<week>
//
// Rough shape of what will eventually live here:
//
// async function loadStandings(leagueId) {
//   const rosters = await fetch(`https://api.sleeper.app/v1/league/${leagueId}/rosters`).then(r => r.json());
//   const users   = await fetch(`https://api.sleeper.app/v1/league/${leagueId}/users`).then(r => r.json());
//   // merge rosters + users, sort by wins/points, render into #standings
// }
//
// loadStandings("YOUR_LEAGUE_ID");
