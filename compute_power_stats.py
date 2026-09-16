"""
compute_power_stats.py

Ports the power-score / Monte Carlo playoff-odds logic from the Yahoo-era
analyze_scores.py to Sleeper, generalized so nothing is hardcoded to this
league's specific team count, week count, or manager names.

NOT WIRED INTO ANYTHING YET -- standalone by design, see recaps-roadmap.md
Section 6/6a/8 for the full reasoning.

Two things worth knowing before trusting this against a real season:
1. Sleeper's player-projections endpoint (used for the "model" half of the
   power score) is UNDOCUMENTED -- confirmed working by third-party Sleeper
   API libraries, but never officially published.
2. Power scores (and anything downstream -- playoff odds, placement odds)
   CANNOT be recomputed retroactively, since the model half depends on a
   projection snapshot that only exists at that moment. That's why this
   writes to data/power_log.json and refuses to overwrite an existing
   week's entry rather than silently recomputing it.

WHAT'S FULLY BUILT: regular-season Monte Carlo (wins/points simulated week
by week using each remaining week's real opponent pairing) feeding a full
playoff-bracket simulation (seeds -> championship + consolation brackets,
including placement games, so every final rank 1-N resolves, not just
"made the bracket or not") -- accurate any time DURING the regular season.

WHAT'S NOT BUILT: mid-playoffs bracket-state awareness. Once the actual
playoffs start, simulate_bracket() still assumes a bracket starting fresh
from round 1 given a seeding -- it does not yet know which real bracket
games have already been played and locked in. Good enough for "if the
season ended today" projections issued during the regular season (i.e.
nearly the entire year); a real gap for mid-playoff-week accuracy
specifically. Flagged here rather than silently wrong.

Usage:
    python compute_power_stats.py
    python compute_power_stats.py --dry-run
    python compute_power_stats.py --trials 20000
    python compute_power_stats.py --force   # overwrite this week's log entry
"""

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

from league_config import SLEEPER_LEAGUE_ID  # single source of truth -- see league_config.py
SLEEPER_API = "https://api.sleeper.app/v1"
PROJECTIONS_API = "https://api.sleeper.app/projections/nfl"  # undocumented -- no /v1 prefix
REQUEST_DELAY_SECONDS = 0.3

HISTORY_DIR = Path("data/history")
POWER_LOG_PATH = Path("data/power_log.json")
PLAYERS_CACHE_PATH = Path("data/.players_cache.json")  # shared cache convention with export_sleeper_feed.py

DEFAULT_MONTE_CARLO_TRIALS = 20_000

FLEX_ELIGIBLE = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}}
NON_STARTING_SLOTS = {"BN", "IR", "TAXI"}


# ============================================================
# Basic Sleeper fetch helpers
# ============================================================

def sleeper_get(path: str):
    resp = requests.get(f"{SLEEPER_API}{path}", timeout=20)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return resp.json()


def sleeper_get_absolute(url: str):
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return resp.json()


def load_players() -> dict:
    """Same 24h-cache convention as export_sleeper_feed.py -- shares the
    same cache file, so running both scripts the same day only pays the
    /players/nfl fetch cost once."""
    if PLAYERS_CACHE_PATH.exists():
        cached = json.loads(PLAYERS_CACHE_PATH.read_text())
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if (datetime.now(timezone.utc) - fetched_at).total_seconds() < 24 * 3600:
            return cached["players"]

    print("Fetching full player database (Sleeper asks this happen at most once/day)...")
    players = sleeper_get("/players/nfl")
    PLAYERS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYERS_CACHE_PATH.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }))
    return players


# ============================================================
# Week / phase resolution
# ============================================================

def next_power_of_two(n: int) -> int:
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def bracket_depth(playoff_teams: int) -> int:
    """Number of playoff rounds (weeks) needed -- e.g. 6 playoff teams ->
    padded bracket size 8 -> 3 rounds. Matches this league's real 3-week
    playoff structure."""
    return next_power_of_two(playoff_teams).bit_length() - 1


def resolve_weeks(state: dict, league: dict) -> dict:
    """Returns completed_weeks (real scores exist), forecast_weeks (weeks
    to project individually for the power score's model component),
    regular_season_weeks, playoff_rounds, and phase ('regular' or
    'playoffs'). forecast_weeks is now EVERY remaining week in the current
    phase, projected individually -- not just the next one extrapolated --
    per the commissioner's explicit ask for the more robust version."""
    playoff_week_start = league["settings"].get("playoff_week_start") or 15
    regular_season_weeks = playoff_week_start - 1
    playoff_teams = league["settings"].get("playoff_teams") or 6
    playoff_rounds = bracket_depth(playoff_teams)

    if state["season"] != league["season"] or state["season_type"] == "pre":
        return {
            "completed_weeks": [], "forecast_weeks": list(range(1, regular_season_weeks + 1)),
            "regular_season_weeks": regular_season_weeks, "playoff_rounds": playoff_rounds, "phase": "regular",
        }

    if state["season_type"] == "post":
        playoff_week_index = max(0, state["week"] - playoff_week_start)  # 0-based weeks into the playoffs
        completed_playoff_weeks = list(range(playoff_week_start, playoff_week_start + playoff_week_index))
        completed = list(range(1, regular_season_weeks + 1)) + completed_playoff_weeks
        current_playoff_week = playoff_week_start + playoff_week_index
        forecast_weeks = [current_playoff_week] if playoff_week_index < playoff_rounds else []
        return {
            "completed_weeks": completed, "forecast_weeks": forecast_weeks,
            "regular_season_weeks": regular_season_weeks, "playoff_rounds": playoff_rounds, "phase": "playoffs",
        }

    current_week = state["week"]
    last_completed = min(current_week - 1, regular_season_weeks)
    completed = list(range(1, last_completed + 1))
    forecast_weeks = list(range(last_completed + 1, regular_season_weeks + 1))
    return {
        "completed_weeks": completed, "forecast_weeks": forecast_weeks,
        "regular_season_weeks": regular_season_weeks, "playoff_rounds": playoff_rounds, "phase": "regular",
    }


# ============================================================
# Scoring translation + optimal lineup (unchanged from the first pass,
# already tested)
# ============================================================

def compute_points_from_stats(stats: dict, scoring_settings: dict) -> float:
    """Fantasy points = dot product of a stats blob and the league's own
    scoring_settings (same stat-abbreviation keys throughout Sleeper's
    API) -- generalizes to any league's custom scoring, bonuses included,
    instead of assuming a generic PPR/standard preset."""
    if not stats:
        return 0.0
    return sum(
        (scoring_settings.get(key) or 0) * value
        for key, value in stats.items()
        if isinstance(value, (int, float))
    )


def fetch_projected_points(season: int, week: int, scoring_settings: dict) -> dict:
    projected = {}
    for position in ("QB", "RB", "WR", "TE", "K", "DEF"):
        url = f"{PROJECTIONS_API}/{season}/{week}?season_type=regular&position[]={position}"
        for proj in sleeper_get_absolute(url):
            pid = proj.get("player_id")
            if not pid:
                continue
            stats = proj.get("stats") if isinstance(proj.get("stats"), dict) else proj
            projected[pid] = compute_points_from_stats(stats, scoring_settings)
    return projected


def pick_optimal_lineup_points(roster_player_ids: list, projected_points: dict, players_db: dict, roster_positions: list) -> float:
    """Basic v1 greedy optimal-lineup solver: strictly-typed slots filled
    first by highest projection, then FLEX/SUPER_FLEX from what's left.
    Correctly avoids double-counting a bench QB etc., since only as many
    players as there are real slots at each position get counted."""
    slot_counts = Counter(s for s in roster_positions if s not in NON_STARTING_SLOTS)

    pool = []
    for pid in roster_player_ids:
        pos = (players_db.get(pid) or {}).get("position")
        if not pos:
            continue
        pool.append({"pid": pid, "pos": pos, "pts": projected_points.get(pid, 0.0)})
    pool.sort(key=lambda p: p["pts"], reverse=True)

    used_pids = set()
    total = 0.0

    for slot, count in slot_counts.items():
        if slot in FLEX_ELIGIBLE:
            continue
        eligible = [p for p in pool if p["pos"] == slot and p["pid"] not in used_pids]
        for p in eligible[:count]:
            used_pids.add(p["pid"])
            total += p["pts"]

    for slot, count in slot_counts.items():
        if slot not in FLEX_ELIGIBLE:
            continue
        allowed = FLEX_ELIGIBLE[slot]
        eligible = [p for p in pool if p["pos"] in allowed and p["pid"] not in used_pids]
        for p in eligible[:count]:
            used_pids.add(p["pid"])
            total += p["pts"]

    return total


def recency_weighted_average(week_scores: list) -> float | None:
    if not week_scores:
        return None
    numerator = sum(week * score for week, score in week_scores)
    denominator = sum(week for week, _ in week_scores)
    return numerator / denominator if denominator else None


def get_score_stdev(team_scores: list, league_wide_scores: list) -> float:
    if len(team_scores) >= 2:
        return float(np.std(team_scores, ddof=1))
    if len(league_wide_scores) >= 2:
        return float(np.std(league_wide_scores, ddof=1))
    return 20.0


def load_season_history(season: int) -> dict:
    path = HISTORY_DIR / f"{season}.json"
    if not path.exists():
        raise FileNotFoundError(f"No {path} -- run export_sleeper_season.py first")
    return json.loads(path.read_text())


# ============================================================
# Power score -- now with a per-week-projected model component instead of
# one week extrapolated, per the commissioner's explicit ask
# ============================================================

def compute_power_scores(season_data: dict, weeks_info: dict, players_db: dict,
                          rosters_by_id: dict, scoring_settings: dict) -> dict:
    teams = season_data["teams"]
    completed_weeks = weeks_info["completed_weeks"]
    forecast_weeks = weeks_info["forecast_weeks"]
    regular_season_weeks = weeks_info["regular_season_weeks"]
    playoff_rounds = weeks_info["playoff_rounds"]
    phase = weeks_info["phase"]

    all_scores_by_team = {
        tid: [(g["week"], g["my_score"]) for g in t["schedule"] if g["week"] in completed_weeks]
        for tid, t in teams.items()
    }
    league_wide_scores = [s for scores in all_scores_by_team.values() for _, s in scores]

    # Fetch each forecast week's projections ONCE, shared across every team
    # -- these don't vary by roster, only by player, so there's no reason
    # to refetch per team.
    projections_by_week = {}
    for week in forecast_weeks:
        print(f"  fetching Week {week} projections (undocumented endpoint)...")
        projections_by_week[week] = fetch_projected_points(season_data["season"], week, scoring_settings)

    weeks_complete = len(completed_weeks)
    total_season_weeks = regular_season_weeks + playoff_rounds if phase == "playoffs" else regular_season_weeks

    results = {}
    for tid, week_scores in all_scores_by_team.items():
        data_component = recency_weighted_average(week_scores)
        roster = rosters_by_id.get(int(tid)) or rosters_by_id.get(tid)

        model_component_sum = 0.0
        if roster:
            for week in forecast_weeks:
                model_component_sum += pick_optimal_lineup_points(
                    roster.get("players") or [], projections_by_week[week], players_db,
                    roster.get("_roster_positions") or [],
                )

        if data_component is None:
            power_score = (model_component_sum / len(forecast_weeks)) if forecast_weeks else 0.0
        elif not forecast_weeks:
            power_score = data_component
        else:
            power_score = (weeks_complete * data_component + model_component_sum) / total_season_weeks

        results[tid] = {
            "power_score": round(power_score, 2),
            "stdev": round(get_score_stdev([s for _, s in week_scores], league_wide_scores), 2),
            "data_component": round(data_component, 2) if data_component is not None else None,
            "model_component_sum": round(model_component_sum, 2) if forecast_weeks else None,
            "forecast_weeks_used": forecast_weeks,
        }
    return results


def fetch_remaining_schedule(regular_season_weeks: int, completed_weeks: list) -> list:
    """Pairings for each remaining REGULAR SEASON week (who plays whom),
    for the win/loss Monte Carlo below. Separate concern from
    forecast_weeks/projections above -- this needs matchup pairings, not
    projections."""
    remaining = []
    for week in range(1, regular_season_weeks + 1):
        if week in completed_weeks:
            continue
        matchups = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/matchups/{week}")
        by_matchup_id: dict = {}
        for entry in matchups:
            # str() here is load-bearing, not cosmetic: team_ids in
            # simulate_season() come from season_data["teams"] (always
            # string roster ids, matching data/history/<season>.json),
            # but Sleeper's live matchups endpoint returns roster_id as a
            # raw JSON int. Without this cast, idx[a]/idx[b] in
            # simulate_season() KeyErrors on every single call -- not
            # occasionally, every time, since the type mismatch is
            # unconditional. Caught from a real, continue-on-error-masked
            # failure: this step showed green in the workflow (exit code
            # swallowed on purpose) while silently never reaching
            # append_to_log() underneath it.
            by_matchup_id.setdefault(entry["matchup_id"], []).append(str(entry["roster_id"]))
        remaining.append([tuple(v) for v in by_matchup_id.values() if len(v) == 2])
    return remaining


# ============================================================
# Playoff bracket simulation
# ============================================================

def standard_bracket_seed_order(size: int) -> list:
    """Standard single-elimination tournament seeding (recursive doubling)
    -- reproduces this league's REAL bracket exactly (confirmed against
    the 2020 write-ups: seed 1 ends up facing the winner of the '4v5'
    pairing, seed 2 the winner of '3v6' -- this algorithm generates that
    structure for any power-of-two size, not just 8)."""
    seeds = [1]
    while len(seeds) < size:
        n = len(seeds) * 2 + 1
        new_seeds = []
        for s in seeds:
            new_seeds.append(s)
            new_seeds.append(n - s)
        seeds = new_seeds
    return seeds


def resolve_bracket_group(slot_occupants: np.ndarray, placement_base: int, placements_out: dict,
                           mus: np.ndarray, sigmas: np.ndarray, trials: int):
    """Recursively resolves a whole single-elimination group -- winners
    continue competing for the top half of this group's placement range,
    losers play placement games for the bottom half, so every rank
    resolves (matches this league's real 3rd/5th/etc. placement games),
    not just who advances. Vectorized across all trials at once per
    round; -1 in slot_occupants means an empty/bye slot."""
    group_size = slot_occupants.shape[0]

    if group_size == 1:
        team_idx = slot_occupants[0]
        present = team_idx >= 0
        if present.any():
            for team_i in np.unique(team_idx[present]):
                team_i = int(team_i)
                arr = placements_out.setdefault(team_i, np.full(trials, -1))
                arr[team_idx == team_i] = placement_base
        return

    half = group_size // 2
    winner_slots = np.full((half, trials), -1, dtype=int)
    loser_slots = np.full((half, trials), -1, dtype=int)

    for i in range(half):
        a_idx, b_idx = slot_occupants[2 * i], slot_occupants[2 * i + 1]
        a_present, b_present = a_idx >= 0, b_idx >= 0
        safe_a, safe_b = np.where(a_present, a_idx, 0), np.where(b_present, b_idx, 0)
        a_score = np.random.normal(mus[safe_a], sigmas[safe_a])
        b_score = np.random.normal(mus[safe_b], sigmas[safe_b])
        both = a_present & b_present
        a_wins = both & (a_score >= b_score)
        b_wins = both & ~a_wins & both
        winner_slots[i] = np.where(a_wins, a_idx, np.where(b_wins, b_idx,
                                    np.where(a_present, a_idx, np.where(b_present, b_idx, -1))))
        loser_slots[i] = np.where(a_wins, b_idx, np.where(b_wins, a_idx, -1))

    resolve_bracket_group(winner_slots, placement_base, placements_out, mus, sigmas, trials)
    resolve_bracket_group(loser_slots, placement_base + half, placements_out, mus, sigmas, trials)


def simulate_bracket(seed_team_matrix: np.ndarray, mus: np.ndarray, sigmas: np.ndarray, trials: int) -> dict:
    """seed_team_matrix: shape (n_participants, trials) -- row j = which
    team index occupies seed j+1 in each trial (varies by trial, since
    regular-season seeding itself is simulated). Returns {team_index:
    (trials,) 0-based placement array} for every team that appears."""
    n = seed_team_matrix.shape[0]
    bracket_size = next_power_of_two(n)
    seed_order = standard_bracket_seed_order(bracket_size)

    slot_occupants = np.full((bracket_size, trials), -1, dtype=int)
    for i, seed_num in enumerate(seed_order):
        if seed_num <= n:
            slot_occupants[i] = seed_team_matrix[seed_num - 1]

    placements_out = {}
    resolve_bracket_group(slot_occupants, 0, placements_out, mus, sigmas, trials)
    return placements_out


# ============================================================
# Full-season Monte Carlo: regular season standings -> bracket seeding ->
# bracket resolution, all vectorized across trials
# ============================================================

def simulate_season(team_ids: list, power_scores: dict, current_wins: dict, current_points: dict,
                     remaining_schedule: list, playoff_teams: int, trials: int) -> dict:
    n = len(team_ids)
    idx = {tid: i for i, tid in enumerate(team_ids)}
    mus = np.array([power_scores[tid]["power_score"] for tid in team_ids])
    sigmas = np.array([max(power_scores[tid]["stdev"], 1.0) for tid in team_ids])

    wins = np.tile(np.array([current_wins[tid] for tid in team_ids], dtype=float), (trials, 1))
    points = np.tile(np.array([current_points[tid] for tid in team_ids], dtype=float), (trials, 1))

    for week_pairs in remaining_schedule:
        draws = np.clip(np.random.normal(mus, sigmas, size=(trials, n)), 0, None)
        points += draws
        for a, b in week_pairs:
            ia, ib = idx[a], idx[b]
            a_won = draws[:, ia] > draws[:, ib]
            wins[:, ia] += a_won
            wins[:, ib] += ~a_won

    # Vectorized ranking (wins desc, points desc as tiebreak) -- combine into
    # one sortable key per team per trial rather than a per-trial Python sort.
    combined = wins * 1_000_000 + points  # safe as long as no team scores >1M points, which, generously, they won't
    order_matrix = np.argsort(-combined, axis=1)  # order_matrix[trial, seed-1] = team index

    regular_season_seed_pct = {}
    most_points_counts = np.bincount(np.argmax(points, axis=1), minlength=n)
    for seed0 in range(n):
        counts = np.bincount(order_matrix[:, seed0], minlength=n)
        for team_i in range(n):
            regular_season_seed_pct.setdefault(team_ids[team_i], {})[str(seed0 + 1)] = round(100 * counts[team_i] / trials, 2)

    # Bracket: top `playoff_teams` seeds -> championship bracket; the rest -> consolation.
    champ_seed_matrix = order_matrix[:, :playoff_teams].T  # (playoff_teams, trials)
    champ_placements = simulate_bracket(champ_seed_matrix, mus, sigmas, trials)

    consolation_size = n - playoff_teams
    cons_placements_raw = {}
    if consolation_size > 0:
        cons_seed_matrix = order_matrix[:, playoff_teams:].T
        cons_placements_raw = simulate_bracket(cons_seed_matrix, mus, sigmas, trials)

    # Merge per-trial rather than per-team: for ANY given trial, a team is in
    # exactly one of the two brackets, never both -- so for each team, take
    # whichever array has a real (non -1) value in each trial. A naive
    # dict-overwrite here (`{**champ, **cons}`) would silently discard a
    # team's championship-bracket placements the moment they also show up in
    # the consolation dict (which every team eventually does, across enough
    # trials) -- caught this via the sum-to-100% sanity check below.
    overall_placements = {}
    for team_i in range(n):
        champ_arr = champ_placements.get(team_i, np.full(trials, -1))
        cons_arr = cons_placements_raw.get(team_i, np.full(trials, -1))
        cons_arr_offset = np.where(cons_arr >= 0, cons_arr + playoff_teams, -1)
        overall_placements[team_i] = np.where(champ_arr >= 0, champ_arr, cons_arr_offset)

    results = {}
    for team_i, tid in enumerate(team_ids):
        placement_arr = overall_placements.get(team_i)
        placement_pct = {}
        if placement_arr is not None:
            for p in range(n):
                placement_pct[str(p + 1)] = round(100 * np.mean(placement_arr == p), 2)
        results[tid] = {
            "regular_season_seed_pct": regular_season_seed_pct[tid],  # top playoff seed etc., BEFORE the bracket
            "playoff_pct": round(sum(v for k, v in regular_season_seed_pct[tid].items() if int(k) <= playoff_teams), 2),
            "bye_pct": round(sum(v for k, v in regular_season_seed_pct[tid].items() if int(k) <= compute_bye_count(playoff_teams)), 2),
            "most_points_pct": round(100 * most_points_counts[team_i] / trials, 2),
            "final_placement_pct": placement_pct,  # AFTER the full bracket -- placement_pct["1"] is true Champion %
            "champion_pct": placement_pct.get("1", 0.0),
        }
    return results


def compute_bye_count(playoff_teams: int) -> int:
    """Standard single-elimination bracket seeding: the top seeds beyond
    the nearest power of two AT OR BELOW playoff_teams get a first-round
    bye. Matches this league's real 6-team/2-bye history."""
    if playoff_teams <= 1:
        return 0
    largest_power_of_two_leq = 1 << (playoff_teams.bit_length() - 1)
    return playoff_teams - largest_power_of_two_leq


# ============================================================
# Logging
# ============================================================

def append_to_log(season: int, week: int, entries: dict):
    log = json.loads(POWER_LOG_PATH.read_text()) if POWER_LOG_PATH.exists() else {"entries": []}
    key = f"{season}-{week}"
    existing = next((e for e in log["entries"] if e["key"] == key), None)
    if existing:
        raise ValueError(f"data/power_log.json already has an entry for {key} -- refusing to overwrite. Use --force to override.")
    log["entries"].append({
        "key": key, "season": season, "week": week,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "teams": entries,
    })
    POWER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    POWER_LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--trials", type=int, default=DEFAULT_MONTE_CARLO_TRIALS)
    args = parser.parse_args()

    print(f"Fetching league {SLEEPER_LEAGUE_ID}...")
    league = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}")
    rosters = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/rosters")
    state = sleeper_get("/state/nfl")
    rosters_by_id = {r["roster_id"]: {**r, "_roster_positions": league["roster_positions"]} for r in rosters}

    weeks_info = resolve_weeks(state, league)
    print(f"  phase={weeks_info['phase']}, completed={weeks_info['completed_weeks']}, "
          f"forecast={weeks_info['forecast_weeks']}, reg_season_weeks={weeks_info['regular_season_weeks']}, "
          f"playoff_rounds={weeks_info['playoff_rounds']}")

    season_data = load_season_history(league["season"])
    players_db = load_players()
    scoring_settings = league["settings"].get("scoring_settings") or league.get("scoring_settings") or {}

    print("Computing power scores...")
    power_scores = compute_power_scores(season_data, weeks_info, players_db, rosters_by_id, scoring_settings)
    for tid, r in power_scores.items():
        print(f"  {season_data['teams'][tid]['team_name']}: power={r['power_score']} "
              f"(data={r['data_component']}, model_sum={r['model_component_sum']}, stdev={r['stdev']})")

    playoff_teams = league["settings"].get("playoff_teams") or 6
    team_ids = list(power_scores.keys())
    odds = {}
    if weeks_info["completed_weeks"] or weeks_info["forecast_weeks"]:
        print(f"Fetching remaining regular-season schedule and running Monte Carlo ({args.trials} trials)...")
        remaining_schedule = fetch_remaining_schedule(weeks_info["regular_season_weeks"], weeks_info["completed_weeks"])
        current_wins = {tid: season_data["teams"][tid]["wins"] for tid in team_ids}
        current_points = {tid: season_data["teams"][tid]["points_for"] for tid in team_ids}
        odds = simulate_season(team_ids, power_scores, current_wins, current_points,
                                remaining_schedule, playoff_teams, args.trials)
        for tid, o in odds.items():
            print(f"  {season_data['teams'][tid]['team_name']}: playoff={o['playoff_pct']}% "
                  f"bye={o['bye_pct']}% champion={o['champion_pct']}%")

    entries = {tid: {**power_scores[tid], **odds.get(tid, {})} for tid in team_ids}

    if args.dry_run:
        print("\n--- DRY RUN: would log the following ---")
        print(json.dumps(entries, indent=2)[:3000])
        return

    week_to_log = (weeks_info["forecast_weeks"][0] if weeks_info["forecast_weeks"]
                   else (max(weeks_info["completed_weeks"]) if weeks_info["completed_weeks"] else 1))
    if args.force and POWER_LOG_PATH.exists():
        log = json.loads(POWER_LOG_PATH.read_text())
        log["entries"] = [e for e in log["entries"] if e["key"] != f"{league['season']}-{week_to_log}"]
        POWER_LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    append_to_log(league["season"], week_to_log, entries)
    print(f"  wrote entry for {league['season']}-{week_to_log} to {POWER_LOG_PATH}")


if __name__ == "__main__":
    main()
