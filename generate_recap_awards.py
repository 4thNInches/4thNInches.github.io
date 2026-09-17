"""
generate_recap_awards.py

Computes this week's recap awards -- the data layer for the "what
actually happened last week" half of recaps.html, mirroring how
generate_matchup_storylines.py is the data layer for the forward-looking
preview half. Structured data only, no prose; render_recap_awards.py
turns this into readable sentences the same pick()-style way
render_matchup_previews.py does for previews.

Award list, v1 (the four staples pattern-mined from the old write-ups,
per recaps-roadmap.md Section 6/6a's candidate list -- the two confirmed
staples plus the two lowest-effort third/fourth candidates):
  - Best Bench       -- most points left on the bench this week
  - You Didn't Lose   -- won despite a bottom-half score, because the
                         specific opponent scored even worse
  - Great Defense     -- won while holding the opponent to a bottom-half
                         score
  - Mega Blowout      -- largest point differential of the week

Award DISPLAY NAMES are sourced from data/league_config.json via
league_config.py, not hardcoded here or in render_recap_awards.py -- per
the architecture spec's explicit call (Section 5) for award names to be
easy to rename for another league. Originally this file had its own
AWARD_NAMES dict as a lighter-weight version of that ask; consolidated
into league_config.py once SLEEPER_LEAGUE_ID turned up hardcoded in four
separate places, at which point a single config file for all of it made
more sense than one config point per value.

"Bottom half" and every other team-count-shaped threshold below is
derived from num_teams (itself derived from however many teams actually
played that week, so a bye week doesn't skew it), never hardcoded -- see
PROJECT_HANDOFF.md Section 4/6 on this exact class of bug already caught
twice in this codebase (the bye-count formula, the "> 6" in
generate_facts.py's bottom-half check).

SELECTION: each award features exactly one instance per week even if more
than one game would technically qualify (e.g. two separate "You Didn't
Lose"-eligible wins in the same week) -- the most extreme qualifying case
is chosen. Keeps the recap to one clean line per award rather than
listing every qualifying game, matching render_matchup_previews.py's
"this schema says what's true, not everything gets used" philosophy, just
applied at the compute layer instead of the render layer since there's
only ever one slot per award type.

Reuses compute_power_stats.py's sleeper_get / load_players / resolve_weeks
(same convention as generate_matchup_storylines.py) rather than
re-implementing Sleeper fetch helpers or week-detection logic.

NOT WIRED INTO ANYTHING YET -- meant to be run and reviewed standalone
first, same as every other script in this pipeline before it.

Usage:
    python generate_recap_awards.py                # auto-detects last completed week
    python generate_recap_awards.py --week 3        # a specific week
    python generate_recap_awards.py --dry-run
"""

import argparse
import json
from pathlib import Path

import compute_power_stats as pws  # sleeper_get, load_players, resolve_weeks
from league_config import AWARD_NAMES  # single source of truth -- see league_config.py

MANAGER_MAPPING_PATH = Path("data/manager_mapping.json")
HISTORY_DIR = Path("data/history")
OUT_DIR = Path("data/recap_storylines")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def build_team_meta_by_roster(users: list, rosters: list, manager_mapping: dict) -> dict:
    """Same construction as generate_matchup_storylines.py's team_meta_by_roster
    block -- kept as its own copy rather than imported, since it's small
    and self-contained and importing it would create a cross-dependency
    between two sibling scripts for no real benefit."""
    user_by_id = {u["user_id"]: u for u in users}
    out = {}
    for r in rosters:
        u = user_by_id.get(r["owner_id"], {})
        team_name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Roster {r['roster_id']}"
        out[r["roster_id"]] = {"manager_id": manager_mapping.get(team_name), "team_name": team_name}
    return out


def _both_sides(pairs: list):
    for a, b in pairs:
        yield a
        yield b


def _player_name(players_db: dict, pid: str) -> str:
    p = players_db.get(pid) or {}
    full = p.get("full_name")
    if full:
        return full
    joined = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    return joined or pid


# ============================================================
# Pairing + shared primitives
# ============================================================

def build_pairs(matchups: list, team_meta: dict) -> list:
    """Groups the flat matchups-endpoint response into (team_a, team_b)
    tuples, each side carrying score + starters/players/players_points +
    team meta -- everything every award below needs, computed once."""
    by_matchup_id: dict = {}
    for entry in matchups:
        by_matchup_id.setdefault(entry["matchup_id"], []).append(entry)

    pairs = []
    for entries in by_matchup_id.values():
        if len(entries) != 2:
            continue  # bye or malformed -- skip rather than guess, same convention as every other script here
        sides = []
        for e in entries:
            meta = team_meta.get(e["roster_id"], {})
            if not meta.get("manager_id"):
                continue
            sides.append({
                "roster_id": e["roster_id"],
                "manager_id": meta["manager_id"], "team_name": meta["team_name"],
                "score": round(e.get("points") or 0.0, 2),
                "starters": e.get("starters") or [],
                "players": e.get("players") or [],
                "players_points": e.get("players_points") or {},
            })
        if len(sides) == 2:
            pairs.append(tuple(sides))
    return pairs


def compute_weekly_rank(pairs: list) -> dict:
    """roster_id -> rank (1 = highest score) across every team that played
    this week -- the same primitive generate_facts.py's luck_facts() uses
    for all-time luck facts (its `weekly_rank`), scoped here to one week."""
    scores = {side["roster_id"]: side["score"] for side in _both_sides(pairs)}
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return {rid: i + 1 for i, (rid, _) in enumerate(ordered)}


# ============================================================
# Per-award computations -- each returns a storyline "data" dict, or None
# if nothing qualifies that week (shouldn't normally happen for Best
# Bench / Mega Blowout, which always have *a* max; can genuinely be None
# for You Didn't Lose / Great Defense most weeks).
# ============================================================

def compute_best_bench(pairs: list, players_db: dict) -> dict | None:
    """Most points left on the bench -- bench = every rostered player NOT
    in that roster's starting lineup that week."""
    best = None
    for side in _both_sides(pairs):
        bench_ids = [pid for pid in side["players"] if pid not in set(side["starters"])]
        if not bench_ids:
            continue
        bench_points = round(sum(side["players_points"].get(pid, 0.0) for pid in bench_ids), 2)
        top_pid = max(bench_ids, key=lambda pid: side["players_points"].get(pid, 0.0))
        candidate = {
            "manager_id": side["manager_id"], "team_name": side["team_name"],
            "bench_points": bench_points,
            "top_bench_player": {
                "name": _player_name(players_db, top_pid),
                "points": round(side["players_points"].get(top_pid, 0.0), 2),
            },
        }
        if best is None or candidate["bench_points"] > best["bench_points"]:
            best = candidate
    return best


def compute_you_didnt_lose(pairs: list, weekly_rank: dict, num_teams: int) -> dict | None:
    """Won despite a bottom-half score -- because the specific opponent
    they played scored even worse. Features the most extreme qualifying
    case (worst winner rank) if more than one game qualifies."""
    threshold = num_teams / 2
    candidates = []
    for a, b in pairs:
        if a["score"] == b["score"]:
            continue
        winner, loser = (a, b) if a["score"] > b["score"] else (b, a)
        if weekly_rank[winner["roster_id"]] > threshold:
            candidates.append((winner, loser))
    if not candidates:
        return None
    winner, loser = max(candidates, key=lambda wl: weekly_rank[wl[0]["roster_id"]])
    return {
        "winner": {"manager_id": winner["manager_id"], "team_name": winner["team_name"],
                   "score": winner["score"], "rank": weekly_rank[winner["roster_id"]]},
        "loser": {"manager_id": loser["manager_id"], "team_name": loser["team_name"],
                  "score": loser["score"], "rank": weekly_rank[loser["roster_id"]]},
    }


def compute_great_defense(pairs: list, weekly_rank: dict, num_teams: int) -> dict | None:
    """Won while holding the opponent to a bottom-half score -- the
    complement of You Didn't Lose (there the WINNER's own score is
    bottom-half; here the LOSER's is). A single game can in principle
    qualify for both awards at once -- that's realistic, not a bug."""
    threshold = num_teams / 2
    candidates = []
    for a, b in pairs:
        if a["score"] == b["score"]:
            continue
        winner, loser = (a, b) if a["score"] > b["score"] else (b, a)
        if weekly_rank[loser["roster_id"]] > threshold:
            candidates.append((winner, loser))
    if not candidates:
        return None
    winner, loser = max(candidates, key=lambda wl: weekly_rank[wl[1]["roster_id"]])
    return {
        "winner": {"manager_id": winner["manager_id"], "team_name": winner["team_name"], "score": winner["score"]},
        "loser": {"manager_id": loser["manager_id"], "team_name": loser["team_name"],
                  "score": loser["score"], "rank": weekly_rank[loser["roster_id"]]},
    }


def compute_mega_blowout(pairs: list) -> dict | None:
    decided = [(a, b) for a, b in pairs if a["score"] != b["score"]]
    if not decided:
        return None
    a, b = max(decided, key=lambda ab: abs(ab[0]["score"] - ab[1]["score"]))
    winner, loser = (a, b) if a["score"] > b["score"] else (b, a)
    return {
        "winner": {"manager_id": winner["manager_id"], "team_name": winner["team_name"], "score": winner["score"]},
        "loser": {"manager_id": loser["manager_id"], "team_name": loser["team_name"], "score": loser["score"]},
        "margin": round(winner["score"] - loser["score"], 2),
    }


# ============================================================
# Assembly
# ============================================================

# ============================================================
# League-wide recap context -- "how the week went" for the league as a
# whole, distinct from the four per-award storylines above. Mined from
# every 2020/2021 write-up: the recurring move is a weekly point total
# (vs. season average), then up to two "this ranks Nth all-time" call-
# outs (closest game, biggest blowout, highest/lowest individual score),
# selected by how extreme they are -- not force-fit every week, since the
# real write-ups often have nothing record-setting to report and just
# state the plain total instead.
#
# Needs the FULL cross-season game/score log (not just this week's live
# matchups) to know what "Nth all-time" even means. Built the same way
# generate_matchup_storylines.py's build_all_games() reads data/history/,
# but flattened for league-wide superlatives rather than head-to-head
# lookups between two specific managers -- kept as its own copy rather
# than imported, same reasoning as build_team_meta_by_roster above.
# ============================================================

def build_historical_logs(history_dir: Path, manager_mapping: dict) -> tuple:
    """Returns (games, scores) across every season in data/history/.
    games: one entry per real-world matchup, deduplicated (not once per
    side). scores: one entry per team-week, every individual score ever
    recorded."""
    games, scores = [], []
    for path in sorted(history_dir.glob("*.json")):
        season_data = json.loads(path.read_text())
        season, teams = season_data["season"], season_data["teams"]
        seen = set()
        for tid, team in teams.items():
            for g in team.get("schedule") or []:
                scores.append({
                    "season": season, "week": g["week"],
                    "manager_id": manager_mapping.get(team["team_name"]),
                    "team_name": team["team_name"], "score": g["my_score"],
                })
                opp_tid = str(g["opponent_team_id"])
                key = tuple(sorted([tid, opp_tid])) + (g["week"],)
                if key in seen:
                    continue
                seen.add(key)
                games.append({
                    "season": season, "week": g["week"],
                    "team_a": team["team_name"], "team_b": g["opponent_name"],
                    "score_a": g["my_score"], "score_b": g["opp_score"],
                    "margin": round(abs(g["my_score"] - g["opp_score"]), 2),
                })
    return games, scores


def _rank_smallest(value: float, population: list) -> int:
    """1-indexed rank if `value` were inserted into `population` sorted
    ascending -- i.e. how many all-time entries are strictly SMALLER.
    Ties don't inflate the rank (strict inequality), so this week's own
    game being present in the population (data/history is already
    updated by the time this runs) doesn't skew its own ranking."""
    return 1 + sum(1 for v in population if v < value)


def _rank_largest(value: float, population: list) -> int:
    """Same as _rank_smallest, but for how many entries are strictly LARGER."""
    return 1 + sum(1 for v in population if v > value)


def compute_recap_context(pairs: list, games_all_time: list, scores_all_time: list) -> dict:
    """Builds the candidate list of league-wide storylines for this week
    -- weekly total plus up to a few "how extreme is this, all-time"
    candidates. Selection of which candidates actually get written about
    happens in the render layer (same split as every other part of this
    pipeline: compute finds what's TRUE, render decides what's USED)."""
    this_week_games, this_week_scores = [], []
    for a, b in pairs:
        this_week_games.append({
            "team_a": a["team_name"], "team_b": b["team_name"],
            "score_a": a["score"], "score_b": b["score"],
            "margin": round(abs(a["score"] - b["score"]), 2),
        })
        this_week_scores.append({"team_name": a["team_name"], "score": a["score"]})
        this_week_scores.append({"team_name": b["team_name"], "score": b["score"]})

    weekly_total = round(sum(s["score"] for s in this_week_scores), 2)
    weekly_avg = round(weekly_total / len(this_week_scores), 2) if this_week_scores else 0.0

    all_time_margins = [g["margin"] for g in games_all_time]
    all_time_scores = [s["score"] for s in scores_all_time]

    candidates = []
    if this_week_games:
        closest = min(this_week_games, key=lambda g: g["margin"])
        candidates.append({"type": "closest_game", "rank": _rank_smallest(closest["margin"], all_time_margins), "data": closest})
        biggest = max(this_week_games, key=lambda g: g["margin"])
        candidates.append({"type": "biggest_blowout", "rank": _rank_largest(biggest["margin"], all_time_margins), "data": biggest})
    if this_week_scores:
        highest = max(this_week_scores, key=lambda s: s["score"])
        candidates.append({"type": "highest_score", "rank": _rank_largest(highest["score"], all_time_scores), "data": highest})
        lowest = min(this_week_scores, key=lambda s: s["score"])
        candidates.append({"type": "lowest_score", "rank": _rank_smallest(lowest["score"], all_time_scores), "data": lowest})

    candidates.sort(key=lambda c: c["rank"])
    return {"weekly_total": weekly_total, "weekly_avg": weekly_avg, "num_teams": len(this_week_scores), "candidates": candidates}


def build_recap_awards(pairs: list, players_db: dict) -> list:
    if not pairs:
        return []
    num_teams = len(list(_both_sides(pairs)))
    weekly_rank = compute_weekly_rank(pairs)

    awards = []
    best_bench = compute_best_bench(pairs, players_db)
    if best_bench:
        awards.append({"type": "best_bench", "name": AWARD_NAMES["best_bench"], "data": best_bench})

    ydl = compute_you_didnt_lose(pairs, weekly_rank, num_teams)
    if ydl:
        awards.append({"type": "you_didnt_lose", "name": AWARD_NAMES["you_didnt_lose"], "data": ydl})

    defense = compute_great_defense(pairs, weekly_rank, num_teams)
    if defense:
        awards.append({"type": "great_defense", "name": AWARD_NAMES["great_defense"], "data": defense})

    blowout = compute_mega_blowout(pairs)
    if blowout:
        awards.append({"type": "mega_blowout", "name": AWARD_NAMES["mega_blowout"], "data": blowout})

    return awards


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", type=int, default=None,
                         help="Week to build the recap for; defaults to the last completed week")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching league {pws.SLEEPER_LEAGUE_ID}...")
    league = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}")
    users = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/users")
    rosters = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/rosters")
    state = pws.sleeper_get("/state/nfl")

    weeks_info = pws.resolve_weeks(state, league)
    week = args.week or (max(weeks_info["completed_weeks"]) if weeks_info["completed_weeks"] else None)
    if week is None:
        print("  no completed weeks yet this season -- nothing to recap. Exiting cleanly.")
        return
    print(f"  building recap awards for week {week}")

    manager_mapping = load_json(MANAGER_MAPPING_PATH)
    team_meta = build_team_meta_by_roster(users, rosters, manager_mapping)
    players_db = pws.load_players()

    print(f"  fetching Week {week} matchup results...")
    matchups = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}/matchups/{week}")
    pairs = build_pairs(matchups, team_meta)
    if not pairs:
        print("  no complete matchup pairs found for this week -- nothing to recap. Exiting cleanly.")
        return

    awards = build_recap_awards(pairs, players_db)

    print("  building league-wide recap context (historical superlatives)...")
    games_all_time, scores_all_time = build_historical_logs(HISTORY_DIR, manager_mapping)
    context = compute_recap_context(pairs, games_all_time, scores_all_time)

    output = {"season": league["season"], "week": week, "awards": awards, "context": context}

    if args.dry_run:
        print("\n--- DRY RUN ---")
        print(json.dumps(output, indent=2))
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{league['season']}-wk{week}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()
