"""
compute_season_plots.py

Computes the "season at a glance" plot data for recaps.html's Season
Plots section -- confirmed in recaps-roadmap.md Section 6b to be a
single, always-current snapshot (NOT tied to whichever week's recap/
preview is being viewed elsewhere on the page), so this simply overwrites
data/season_plots.json in full every run, the same way generate_facts.py
overwrites facts.json every run -- no per-week history file needed here.

Covers three of the four "season at a glance" plots from the architecture
spec (flex distribution, Lady Luck/Xwins, positional PPW). The fourth,
power-rank history, is intentionally NOT duplicated here -- it already
exists as data/power_log.json (written weekly by compute_power_stats.py,
one entry per week, never overwritten), and recaps.js should read that
file directly for that chart. Repackaging it here would create two
sources of truth for the same numbers.

Lady Luck / Xwins formula (recaps-roadmap.md Section 6, ported from
analyze_scores.py's rank-based closed form -- confirmed to be what
analyze_scores.py actually plots, NOT the more elaborate if_manager
schedule-swap-simulation function that also exists in that file but
doesn't feed the Lady Luck chart):

    xwin = sum over every completed week of (num_teams - weekly_rank) / (num_teams - 1)

Where weekly_rank is that team's rank by raw score that week (1 = best) --
the same primitive generate_recap_awards.py uses for You Didn't Lose /
Great Defense, computed here from data/history/<season>.json's
already-stored team-level weekly scores. No live Sleeper call needed for
this part.

Positional PPW and flex distribution DO need a live call per completed
week -- data/history/ only stores team-level totals, not per-slot detail
(same gap Best Bench hit). Unlike analyze_scores.py's Yahoo-era approach
(reverse-engineering which slot was FLEX from exported roster JSON files
and a hand-maintained team-mapping file), Sleeper's `starters` array is
already positionally aligned with the league's own `roster_positions`
list -- no guessing needed, so this is actually simpler to port than the
original, not just "portable."

"Ranked league-wide" (positional PPW) is left as per-team data here,
NOT pre-sorted into a leaderboard -- per the "charts are live-rendered in
the browser" design principle, ranking/sorting for display belongs in the
render layer, not baked into this file.

NOT WIRED INTO ANYTHING YET -- meant to be run and reviewed standalone
first, same as every other script in this pipeline before it.

Usage:
    python compute_season_plots.py
    python compute_season_plots.py --dry-run
"""

import argparse
import json
from pathlib import Path

import compute_power_stats as pws  # sleeper_get, load_players, resolve_weeks

HISTORY_DIR = Path("data/history")
MANAGER_MAPPING_PATH = Path("data/manager_mapping.json")
OUT_PATH = Path("data/season_plots.json")

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
FLEX_ELIGIBLE = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}}
NON_STARTING_SLOTS = {"BN", "IR", "TAXI"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def load_season_history(season: int) -> dict:
    path = HISTORY_DIR / f"{season}.json"
    if not path.exists():
        raise FileNotFoundError(f"No {path} -- run export_sleeper_season.py first")
    return json.loads(path.read_text())


# ============================================================
# Lady Luck / Xwins -- from data/history/, no live Sleeper call needed
# ============================================================

def compute_weekly_ranks_by_team(season_data: dict, completed_weeks: list) -> dict:
    """tid -> {week: rank} across every completed week, 1 = highest score
    that week. Same primitive as generate_recap_awards.py's
    compute_weekly_rank, built from the season history file instead of a
    live matchups call, since every completed week's team-level score
    already lives there."""
    teams = season_data["teams"]
    scores_by_week: dict = {w: [] for w in completed_weeks}
    for tid, team in teams.items():
        for g in team.get("schedule") or []:
            if g["week"] in scores_by_week:
                scores_by_week[g["week"]].append((tid, g["my_score"]))

    ranks: dict = {tid: {} for tid in teams}
    for week, scores in scores_by_week.items():
        ordered = sorted(scores, key=lambda ts: -ts[1])
        for i, (tid, _) in enumerate(ordered):
            ranks[tid][week] = i + 1
    return ranks


def compute_xwins(ranks_by_team: dict, num_teams: int, completed_weeks: list) -> dict:
    """xwin = sum over every completed week of (num_teams - rank) / (num_teams - 1)
    -- the closed-form 'what fraction of the league would you have beaten
    this week, given your rank' shortcut, per recaps-roadmap.md Section 6."""
    if num_teams <= 1:
        return {tid: 0.0 for tid in ranks_by_team}
    xwins = {}
    for tid, weeks in ranks_by_team.items():
        total = sum((num_teams - weeks[w]) / (num_teams - 1) for w in completed_weeks if w in weeks)
        xwins[tid] = round(total, 2)
    return xwins


# ============================================================
# What-If Schedule grid -- distinct from the Xwins closed-form above.
# This is the FULL pairwise "if manager A had played manager B's exact
# schedule, what would A's record be" swap, matching analyze_scores.py's
# original get_record(if_manager=...) capability, which recaps-roadmap.md
# Section 6 concluded was NOT what the Lady Luck chart itself plots -- but
# is still a real, distinct, useful visualization on its own (a request
# to add it back, seeing the original matplotlib output side by side,
# confirmed these are two different plots, not duplicates).
#
# Every cell is computable directly from data/history/<season>.json's
# existing per-team schedule -- no live Sleeper call needed. The key
# simplification: B's schedule entry for week W already stores
# B's-opponent's score directly (as opp_score), so "who did B play, and
# what did that opponent score" never needs a separate lookup.
#
# One real edge case this needs to handle explicitly: the week A and B
# actually played each other. "If A had B's schedule" can't sensibly mean
# "A plays A" for that week -- a manager can never play themselves, full
# stop -- so that week uses A's real result instead of the swap formula.
# (An earlier version of this function got this wrong: comparing A's
# score to B's-opponent's-score, when B's opponent that week WAS A,
# reduces to comparing A's score to itself -- a guaranteed tie regardless
# of what actually happened, which is a bug, not a feature.)
# ============================================================

def compute_what_if_grid(season_data: dict, manager_mapping: dict, completed_weeks: list) -> dict:
    teams = season_data["teams"]
    completed_set = set(completed_weeks)

    manager_by_tid: dict = {}
    weekly_by_tid: dict = {}
    for tid, team in teams.items():
        mgr = manager_mapping.get(team["team_name"])
        if not mgr:
            continue  # unmapped team_name -- same guard used everywhere else in this pipeline
        manager_by_tid[tid] = mgr
        weekly_by_tid[tid] = {g["week"]: g for g in (team.get("schedule") or []) if g["week"] in completed_set}

    # tid -> {week: opponent_tid actually faced that week} -- needed below
    # to detect the one case the plain swap formula can't handle sensibly:
    # the week A and B actually played each other for real. "If A had B's
    # schedule" for THAT particular week can't mean "A plays A" -- that
    # week's schedule already IS the real A-vs-B game, for both of them,
    # so there's nothing to swap. Use A's real result for that week
    # instead of comparing A's score against itself (which would force a
    # nonsensical guaranteed tie every single time two managers were
    # actually matched up, no matter what the real result was).
    opponent_tid_by_week: dict = {
        tid: {g["week"]: str(g["opponent_team_id"]) for g in (team.get("schedule") or []) if g["week"] in completed_set}
        for tid, team in teams.items()
    }

    cells: dict = {}
    for a_tid, a_mgr in manager_by_tid.items():
        row: dict = {}
        for b_tid, b_mgr in manager_by_tid.items():
            wins = losses = ties = 0
            for week in completed_weeks:
                a_game = weekly_by_tid[a_tid].get(week)
                b_game = weekly_by_tid[b_tid].get(week)
                if not a_game or not b_game:
                    continue  # bye or missing data that week -- skip rather than guess
                if a_tid != b_tid and opponent_tid_by_week[a_tid].get(week) == b_tid:
                    # A and B were each other's real opponent this week --
                    # use A's own real result rather than a self-comparison.
                    a_score, opp_score = a_game["my_score"], a_game["opp_score"]
                else:
                    a_score = a_game["my_score"]
                    opp_score = b_game["opp_score"]  # whoever B actually played that week, and what they scored
                if a_score > opp_score:
                    wins += 1
                elif a_score < opp_score:
                    losses += 1
                else:
                    ties += 1
            row[b_mgr] = {"wins": wins, "losses": losses, "ties": ties}
        cells[a_mgr] = row

    # Delta relative to each row's own diagonal (its real actual record,
    # which the swap trivially reproduces when a_mgr == b_mgr -- the
    # diagonal never hits the "real opponent" branch above, since a team
    # is never recorded as its own opponent, so it always falls through
    # to the plain formula, which for a_tid == b_tid reduces to comparing
    # a team's real score against its own real opponent's score: its
    # actual result, exactly).
    for a_mgr, row in cells.items():
        actual_wins = row[a_mgr]["wins"]
        for b_mgr, cell in row.items():
            cell["delta"] = cell["wins"] - actual_wins

    return {
        "managers": list(manager_by_tid.values()),
        "team_names": {mgr: teams[tid]["team_name"] for tid, mgr in manager_by_tid.items()},
        "cells": cells,
    }


# ============================================================
# Positional PPW + flex distribution -- needs a live per-week matchups
# call, since data/history/ only stores team-level totals.
# ============================================================

def resolve_flex_slot(slot: str, player_pos: str) -> str | None:
    """Returns the ACTUAL position credit for a starting slot -- itself
    for a concrete slot (QB/RB/WR/TE/K/DEF), or the player's real position
    if the slot is FLEX/SUPER_FLEX. None if the slot isn't a scored
    starting position at all (shouldn't normally happen for an entry that
    made it into `starters`, but never trust an external API's shape
    unconditionally)."""
    if slot in POSITIONS:
        return slot
    if slot in FLEX_ELIGIBLE and player_pos in FLEX_ELIGIBLE[slot]:
        return player_pos
    return None


def accumulate_week(matchups: list, roster_positions: list, players_db: dict,
                     ppw_sum: dict, ppw_starts: dict, flex_counts: dict):
    """Mutates the three accumulator dicts in place with one week's worth
    of starters. ppw_sum/ppw_starts are {tid: {pos: value}}; flex_counts
    is league-wide {pos: count}, per the spec's "league-wide" framing for
    flex distribution specifically (unlike positional PPW, which stays
    per-team)."""
    starting_slots = [s for s in roster_positions if s not in NON_STARTING_SLOTS]
    for entry in matchups:
        tid = str(entry["roster_id"])
        starters = entry.get("starters") or []
        points = entry.get("players_points") or {}
        for slot, pid in zip(starting_slots, starters):
            if not pid or pid == "0":
                continue  # empty slot that week (bye/injury with no fill)
            player_pos = (players_db.get(pid) or {}).get("position")
            if not player_pos:
                continue
            credited_pos = resolve_flex_slot(slot, player_pos)
            if not credited_pos:
                continue
            ppw_sum.setdefault(tid, {}).setdefault(credited_pos, 0.0)
            ppw_starts.setdefault(tid, {}).setdefault(credited_pos, 0)
            ppw_sum[tid][credited_pos] += points.get(pid, 0.0)
            ppw_starts[tid][credited_pos] += 1
            if slot in FLEX_ELIGIBLE:
                flex_counts[credited_pos] = flex_counts.get(credited_pos, 0) + 1


def finalize_ppw(ppw_sum: dict, ppw_starts: dict) -> dict:
    return {
        tid: {pos: round(ppw_sum[tid][pos] / ppw_starts[tid][pos], 2) for pos in ppw_sum[tid]}
        for tid in ppw_sum
    }


def finalize_flex_distribution(flex_counts: dict) -> dict:
    total = sum(flex_counts.values())
    if not total:
        return {}
    return {pos: round(100 * count / total, 1) for pos, count in flex_counts.items()}


# ============================================================
# Assembly
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching league {pws.SLEEPER_LEAGUE_ID}...")
    league = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}")
    users = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/users")
    rosters = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/rosters")
    state = pws.sleeper_get("/state/nfl")

    weeks_info = pws.resolve_weeks(state, league)
    completed_weeks = weeks_info["completed_weeks"]
    if not completed_weeks:
        print("  no completed weeks yet this season -- nothing to plot. Exiting cleanly.")
        return

    manager_mapping = load_json(MANAGER_MAPPING_PATH)
    season_data = load_season_history(league["season"])
    teams = season_data["teams"]
    num_teams = len(teams)

    print(f"  computing Lady Luck / Xwins across {len(completed_weeks)} completed week(s)...")
    ranks_by_team = compute_weekly_ranks_by_team(season_data, completed_weeks)
    xwins = compute_xwins(ranks_by_team, num_teams, completed_weeks)

    print("  computing What-If Schedule grid...")
    what_if_grid = compute_what_if_grid(season_data, manager_mapping, completed_weeks)

    players_db = pws.load_players()
    roster_positions = league["roster_positions"]

    ppw_sum: dict = {}
    ppw_starts: dict = {}
    flex_counts: dict = {}
    for week in completed_weeks:
        print(f"  fetching Week {week} starters for positional PPW / flex distribution...")
        matchups = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/matchups/{week}")
        accumulate_week(matchups, roster_positions, players_db, ppw_sum, ppw_starts, flex_counts)

    positional_ppw_by_roster = finalize_ppw(ppw_sum, ppw_starts)
    flex_distribution = finalize_flex_distribution(flex_counts)

    user_by_id = {u["user_id"]: u for u in users}
    team_output = {}
    for r in rosters:
        rid = str(r["roster_id"])
        team = teams.get(rid)
        if not team:
            continue
        u = user_by_id.get(r["owner_id"], {})
        team_name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or team["team_name"]
        manager_id = manager_mapping.get(team_name) or manager_mapping.get(team["team_name"])
        wins = team["wins"]
        team_xwins = xwins.get(rid, 0.0)
        team_output[manager_id or rid] = {
            "team_name": team_name,
            "wins": wins,
            "losses": team["losses"],
            "ties": team["ties"],
            "xwins": team_xwins,
            "luck_delta": round(wins - team_xwins, 2),
            "positional_ppw": positional_ppw_by_roster.get(rid, {}),
        }

    output = {
        "season": league["season"],
        "weeks_complete": len(completed_weeks),
        "teams": team_output,
        "flex_distribution": flex_distribution,
        "what_if_grid": what_if_grid,
    }

    if args.dry_run:
        print("\n--- DRY RUN ---")
        print(json.dumps(output, indent=2))
        return

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
