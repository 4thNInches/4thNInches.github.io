// 4th & Inches — site script

const SLEEPER_LEAGUE_ID = "1392229432336347136";

const PLACE_LABEL = { first: "1st", second: "2nd", third: "3rd" };
const PLACE_ICON = { first: "\u{1F3C6}", second: "\u{1F948}", third: "\u{1F949}" };

async function loadStandings(leagueId) {
  const container = document.getElementById("standings-table");
  if (!container) return;

  let rosters, users;
  try {
    [rosters, users] = await Promise.all([
      fetch(`https://api.sleeper.app/v1/league/${leagueId}/rosters`).then(r => {
        if (!r.ok) throw new Error(`rosters HTTP ${r.status}`);
        return r.json();
      }),
      fetch(`https://api.sleeper.app/v1/league/${leagueId}/users`).then(r => {
        if (!r.ok) throw new Error(`users HTTP ${r.status}`);
        return r.json();
      }),
    ]);
  } catch (err) {
    container.innerHTML = `<p class="loading-msg">Couldn't load standings from Sleeper (${err.message}). Sleeper's API is public and needs no auth, so this is usually a temporary network issue — try refreshing.</p>`;
    return;
  }

  const usersById = {};
  users.forEach(u => { usersById[u.user_id] = u; });

  const rows = rosters.map(r => {
    const user = usersById[r.owner_id] || {};
    const teamName = (user.metadata && user.metadata.team_name) || user.display_name || "Unclaimed team";
    const managerName = user.display_name || "\u2014";
    const avatarId = (user.metadata && user.metadata.avatar) || user.avatar || null;
    const settings = r.settings || {};
    return {
      teamName,
      managerName,
      avatarId,
      wins: settings.wins || 0,
      losses: settings.losses || 0,
      ties: settings.ties || 0,
      pointsFor: pointsFromSettings(settings.fpts, settings.fpts_decimal),
      pointsAgainst: pointsFromSettings(settings.fpts_against, settings.fpts_against_decimal),
    };
  });

  rows.sort((a, b) => (b.wins - a.wins) || (b.pointsFor - a.pointsFor));

  const allZero = rows.every(row => row.wins === 0 && row.losses === 0 && row.ties === 0);

  const bodyRows = rows.map((row, i) => `
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
      <td class="standings-pts">${row.pointsAgainst.toFixed(1)}</td>
    </tr>
  `).join("");

  container.innerHTML = `
    ${allZero ? `<p class="loading-msg standings-note">Preseason \u2014 records are 0-0 until Week 1 kicks off.</p>` : ""}
    <table class="standings-real-table">
      <thead>
        <tr>
          <th scope="col">#</th>
          <th scope="col">Team</th>
          <th scope="col">Record</th>
          <th scope="col">PF</th>
          <th scope="col">PA</th>
        </tr>
      </thead>
      <tbody>${bodyRows}</tbody>
    </table>
  `;
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
  renderMostTitles(seasons);
}

const TICKER_ROTATE_MS = 6000;
const TICKER_FADE_MS = 350;

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

function renderMostTitles(seasons) {
  const el = document.getElementById("most-titles-value");
  if (!el) return;

  const golds = {};
  seasons.forEach(s => {
    const m = s.first.manager;
    golds[m] = (golds[m] || 0) + 1;
  });

  const [topManager, topCount] = Object.entries(golds).sort((a, b) => b[1] - a[1])[0];
  el.textContent = `${topCount} \u2014 ${topManager}`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", () => {
  loadStandings(SLEEPER_LEAGUE_ID);
  loadChampions();
});

document.addEventListener("DOMContentLoaded", () => {
  loadStandings(SLEEPER_LEAGUE_ID);
  loadChampions();
  loadTicker();
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
