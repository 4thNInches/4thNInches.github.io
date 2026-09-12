"""
generate_matchup_storylines.py

The actual storyline-selection engine's data layer: for a given week's
matchups, computes every storyline type from recaps-roadmap.md Section 11
that qualifies, in the agreed schema -- structured data only, no prose.
A later template layer (not built here) turns this into actual sentences
with phrasing variants, the same way generate_facts.py's pick() already
does for the ticker.

Deliberately reuses compute_power_stats.py's projection-fetching and
scoring-translation machinery for the roster_strength storyline, rather
than re-implementing it -- same undocumented-endpoint caveat applies here
as there.

NOT WIRED INTO ANYTHING YET, same as compute_power_stats.py before it --
meant to be run and reviewed standalone first.

Usage:
    python generate_matchup_storylines.py                  # this week's matchups, current season
    python generate_matchup_storylines.py --week 1          # a specific week
    python generate_matchup_storylines.py --dry-run
"""

import argparse
import json
from pathlib import Path

import compute_power_stats as pws  # reuses sleeper_get, fetch_projected_points, load_players, etc.

HISTORY_DIR = Path("data/history")
MANAGER_MAPPING_PATH = Path("data/manager_mapping.json")
TEAM_FLAVOR_PATH = Path("data/team_flavor.json")
RIVALRY_LORE_PATH = Path("data/rivalry_lore.json")
OUT_DIR = Path("data/storylines")

ROSTER_STRENGTH_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


# ============================================================
# Shared game log: every real-world game across every season, deduplicated
# and resolved to manager_ids -- the foundation every head-to-head-style
# storyline reads from.
# ============================================================

def _add_playoff_game(games: list, g: dict, round_name: str, season: int, teams: dict, manager_mapping: dict):
    if not g or not g.get("team1_id"):
        return
    t1, t2 = teams.get(str(g["team1_id"])), teams.get(str(g["team2_id"]))
    if not t1 or not t2:
        return
    mgr_a, mgr_b = manager_mapping.get(t1["team_name"]), manager_mapping.get(t2["team_name"])
    if not mgr_a or not mgr_b:
        return
    games.append({
        "season": season, "week": g.get("week"), "phase": "playoff", "round": round_name,
        "manager_a": mgr_a, "manager_b": mgr_b,
        "score_a": g["team1_score"], "score_b": g["team2_score"],
    })


def build_all_games(history_dir: Path, manager_mapping: dict) -> list:
    """Flat list of every game ever played, deduplicated (a regular-season
    game appears once, not once per team's own schedule entry), resolved
    to manager_ids rather than team names so renames don't fragment a
    manager's history."""
    games = []
    for path in sorted(history_dir.glob("*.json")):
        season_data = json.loads(path.read_text())
        season, teams = season_data["season"], season_data["teams"]

        seen = set()
        for tid, team in teams.items():
            for g in team.get("schedule") or []:
                opp_tid = str(g["opponent_team_id"])
                key = tuple(sorted([tid, opp_tid])) + (g["week"],)
                if key in seen:
                    continue
                seen.add(key)
                mgr_a, mgr_b = manager_mapping.get(team["team_name"]), manager_mapping.get(g["opponent_name"])
                if not mgr_a or not mgr_b:
                    continue
                games.append({
                    "season": season, "week": g["week"], "phase": "regular",
                    "manager_a": mgr_a, "manager_b": mgr_b,
                    "score_a": g["my_score"], "score_b": g["opp_score"],
                })

        playoffs = season_data.get("playoffs") or {}
        for round_name in ("quarterfinal", "semifinal", "fifth_place"):
            for g in playoffs.get(round_name) or []:
                _add_playoff_game(games, g, round_name, season, teams, manager_mapping)
        for round_name in ("third_place", "championship"):
            _add_playoff_game(games, playoffs.get(round_name), round_name, season, teams, manager_mapping)

    return games


def _games_between(games: list, manager_a: str, manager_b: str, phase: str | None = None) -> list:
    pair = {manager_a, manager_b}
    return [g for g in games if {g["manager_a"], g["manager_b"]} == pair and (phase is None or g["phase"] == phase)]


def _score_for(g: dict, manager_id: str) -> float:
    return g["score_a"] if g["manager_a"] == manager_id else g["score_b"]


def _winner(g: dict) -> str | None:
    if g["score_a"] == g["score_b"]:
        return None
    return g["manager_a"] if g["score_a"] > g["score_b"] else g["manager_b"]


# ============================================================
# Storyline computations -- each returns None if it doesn't qualify for
# this pair, matching "the list only holds what's actually true" from
# the schema design.
# ============================================================

def compute_head_to_head(games: list, manager_a: str, manager_b: str) -> dict | None:
    relevant = sorted(_games_between(games, manager_a, manager_b, phase="regular"),
                       key=lambda g: (g["season"], g["week"]))
    if not relevant:
        return None

    wins = {manager_a: 0, manager_b: 0}
    total = {manager_a: 0.0, manager_b: 0.0}
    for g in relevant:
        total[manager_a] += _score_for(g, manager_a)
        total[manager_b] += _score_for(g, manager_b)
        w = _winner(g)
        if w:
            wins[w] += 1

    streak_manager, streak_count = None, 0
    for g in reversed(relevant):
        w = _winner(g)
        if w is None:
            break
        if streak_manager is None:
            streak_manager, streak_count = w, 1
        elif w == streak_manager:
            streak_count += 1
        else:
            break

    n = len(relevant)
    return {
        "career_record": wins,
        "avg_score": {manager_a: round(total[manager_a] / n, 2), manager_b: round(total[manager_b] / n, 2)},
        "current_streak": {"manager_id": streak_manager, "count": streak_count} if streak_manager else None,
    }


def compute_postseason_history(games: list, manager_a: str, manager_b: str) -> dict | None:
    relevant = sorted(_games_between(games, manager_a, manager_b, phase="playoff"),
                       key=lambda g: (g["season"], g.get("week") or 0))
    if not relevant:
        return None
    meetings = [{
        "season": g["season"], "round": g["round"], "winner": _winner(g),
        "score": {g["manager_a"]: g["score_a"], g["manager_b"]: g["score_b"]},
    } for g in relevant]
    return {"meetings": meetings}


def compute_notable_matchup(games: list, manager_a: str, manager_b: str) -> dict | None:
    """v1: surfaces the highest-combined-score meeting between this pair
    specifically. Deliberately one simple metric for now -- expanding to
    also consider biggest blowout / closest margin as alternatives is a
    natural extension once this framework is proven, not needed to get a
    workable version in place."""
    relevant = _games_between(games, manager_a, manager_b)
    if not relevant:
        return None
    g = max(relevant, key=lambda g: g["score_a"] + g["score_b"])
    return {
        "season": g["season"], "week": g.get("week"), "phase": g["phase"],
        "score": {manager_a: _score_for(g, manager_a), manager_b: _score_for(g, manager_b)},
        "note_key": "highest_combined_score_between_them",
    }


def lookup_rivalry_lore(rivalry_lore_data: dict, manager_a: str, manager_b: str) -> dict | None:
    pair = {manager_a, manager_b}
    for key, entry in rivalry_lore_data.items():
        if key.startswith("_"):
            continue
        if entry.get("confidence") != "established":
            continue  # matches rivalry_lore.json's own documented convention
        if pair <= set(entry.get("managers") or []):
            return {"lore_key": key}
    return None


def lookup_team_flavor(team_flavor_data: dict, manager_id: str, current_team_name: str) -> dict | None:
    entry = team_flavor_data.get(manager_id)
    if not entry:
        return None
    if entry.get("scope") == "team_name" and entry.get("applies_to_team_name") != current_team_name:
        return None  # retired via rename, exactly as team_flavor.json's own README describes
    return {"manager_id": manager_id, "flavor_key": manager_id}


def compute_roster_strength(roster_a: dict, roster_b: dict, projected_points: dict, players_db: dict) -> dict | None:
    """Best projected player at each position, per team -- a simpler
    comparison than the full optimal-lineup solver (that's a per-team
    concept; here we want 'who has the stronger unit at this position',
    which is a pairwise comparison instead)."""
    def best_by_position(roster):
        by_pos = {}
        for pid in roster.get("players") or []:
            pos = (players_db.get(pid) or {}).get("position")
            if pos not in ROSTER_STRENGTH_POSITIONS:
                continue
            by_pos[pos] = max(by_pos.get(pos, 0.0), projected_points.get(pid, 0.0))
        return by_pos

    a_by_pos, b_by_pos = best_by_position(roster_a), best_by_position(roster_b)
    edges = []
    for pos in ROSTER_STRENGTH_POSITIONS:
        a_pts, b_pts = a_by_pos.get(pos, 0.0), b_by_pos.get(pos, 0.0)
        if a_pts == 0 and b_pts == 0:
            continue
        if a_pts == b_pts:
            continue
        edges.append({
            "position": pos, "leader": "a" if a_pts > b_pts else "b",
            "projected_ppw": {"a": round(a_pts, 2), "b": round(b_pts, 2)},
        })
    return {"positional_edge": edges} if edges else None


# ============================================================
# Assembly
# ============================================================

def build_matchup_storylines(games: list, manager_a: str, manager_b: str,
                              team_a_meta: dict, team_b_meta: dict,
                              rivalry_lore_data: dict, team_flavor_data: dict,
                              roster_strength_data: dict | None) -> dict:
    storylines = []

    h2h = compute_head_to_head(games, manager_a, manager_b)
    if h2h:
        storylines.append({"type": "head_to_head", "data": h2h})

    playoff = compute_postseason_history(games, manager_a, manager_b)
    if playoff:
        storylines.append({"type": "postseason_history", "data": playoff})

    notable = compute_notable_matchup(games, manager_a, manager_b)
    if notable:
        storylines.append({"type": "notable_matchup", "data": notable})

    lore = lookup_rivalry_lore(rivalry_lore_data, manager_a, manager_b)
    if lore:
        storylines.append({"type": "rivalry_lore", "data": lore})

    for mgr, meta in ((manager_a, team_a_meta), (manager_b, team_b_meta)):
        flavor = lookup_team_flavor(team_flavor_data, mgr, meta["team_name"])
        if flavor:
            storylines.append({"type": "team_flavor", "data": flavor})

    if roster_strength_data:
        storylines.append({"type": "roster_strength", "data": roster_strength_data})

    return {"team_a": team_a_meta, "team_b": team_b_meta, "storylines": storylines}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", type=int, default=None, help="Week to build previews for; defaults to the current/target week")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching league {pws.SLEEPER_LEAGUE_ID}...")
    league = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}")
    users = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/users")
    rosters = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/rosters")
    state = pws.sleeper_get("/state/nfl")
    rosters_by_id = {r["roster_id"]: {**r, "_roster_positions": league["roster_positions"]} for r in rosters}

    weeks_info = pws.resolve_weeks(state, league)
    week = args.week or (weeks_info["forecast_weeks"][0] if weeks_info["forecast_weeks"] else 1)
    print(f"  building storylines for week {week}")

    manager_mapping = load_json(MANAGER_MAPPING_PATH)
    team_flavor_data = load_json(TEAM_FLAVOR_PATH)
    rivalry_lore_data = load_json(RIVALRY_LORE_PATH)
    players_db = pws.load_players()

    print("  building the full cross-season game log...")
    games = build_all_games(HISTORY_DIR, manager_mapping)
    print(f"  {len(games)} games loaded across {len(list(HISTORY_DIR.glob('*.json')))} seasons")

    scoring_settings = league["settings"].get("scoring_settings") or league.get("scoring_settings") or {}
    print(f"  fetching Week {week} projections for roster_strength...")
    projected_points = pws.fetch_projected_points(league["season"], week, scoring_settings)

    user_by_id = {u["user_id"]: u for u in users}
    team_meta_by_roster = {}
    for r in rosters:
        u = user_by_id.get(r["owner_id"], {})
        team_name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Roster {r['roster_id']}"
        team_meta_by_roster[r["roster_id"]] = {
            "manager_id": manager_mapping.get(team_name),
            "team_name": team_name,
        }

    print(f"  fetching Week {week} matchup pairings...")
    matchups = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/matchups/{week}")
    by_matchup_id: dict = {}
    for entry in matchups:
        by_matchup_id.setdefault(entry["matchup_id"], []).append(entry["roster_id"])

    previews = []
    for pair in by_matchup_id.values():
        if len(pair) != 2:
            continue
        roster_a_id, roster_b_id = pair
        team_a_meta, team_b_meta = team_meta_by_roster[roster_a_id], team_meta_by_roster[roster_b_id]
        if not team_a_meta["manager_id"] or not team_b_meta["manager_id"]:
            continue

        roster_strength = compute_roster_strength(
            rosters_by_id[roster_a_id], rosters_by_id[roster_b_id], projected_points, players_db,
        )
        previews.append(build_matchup_storylines(
            games, team_a_meta["manager_id"], team_b_meta["manager_id"],
            team_a_meta, team_b_meta, rivalry_lore_data, team_flavor_data, roster_strength,
        ))

    output = {"season": league["season"], "week": week, "matchups": previews}

    if args.dry_run:
        print("\n--- DRY RUN ---")
        print(json.dumps(output, indent=2)[:4000])
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{league['season']}-wk{week}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()
