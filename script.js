// 4th & Inches — site script

const PLACE_LABEL = { first: "1st", second: "2nd", third: "3rd" };
const PLACE_ICON = { first: "\u{1F3C6}", second: "\u{1F948}", third: "\u{1F949}" };

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

document.addEventListener("DOMContentLoaded", loadChampions);

// Below: nothing live yet for standings/stats/feed. This is the spot where
// Sleeper data will get fetched and dropped into the remaining placeholder
// cards in index.html (#standings, #feed, and the other #stats cards).
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
