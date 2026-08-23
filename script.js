// 4th & Inches — site script
//
// Nothing live yet. This is the spot where Sleeper data will get fetched
// and dropped into the placeholder cards in index.html (#standings, #stats,
// #history, #feed).
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
