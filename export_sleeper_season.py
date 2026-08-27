"""
export_sleeper_season.py

Pulls the current season from Sleeper's API and writes it into
data/history/<season>.json using the exact same schema as the Yahoo-era
files (export_yahoo_history.py) -- team records, weekly schedules, and a
playoffs section. generate_facts.py doesn't know or care whether a given
season came from Yahoo or Sleeper; it just reads whatever's in data/history/.

Unlike the Yahoo scripts, this needs no auth at all -- Sleeper's API is
fully public, read-only, no key, no OAuth. That's the whole reason this
can run unattended on a schedule (see the accompanying GitHub Actions
workflow) instead of needing a session cookie refreshed by hand.

CURRENT SCOPE: regular season only. Playoff-bracket parsing (Sleeper's
winners_bracket/losers_bracket endpoints) is NOT implemented yet -- this
league's playoffs are months away as of when this was written, and there's
no real bracket data yet to build or test that against. Running this
script right now produces a season file with an empty/placeholder
playoffs section (generate_facts.py already handles that gracefully --
see is_season_complete() and the guards it feeds). Add playoff-bracket
support before this league's playoffs start; that's real, scoped future
work, not an oversight.

Identity: Sleeper's own team_name field is only set for about half of
this league's managers. Rather than depend on that being consistently
set, every roster's manager identity is resolved directly from
SLEEPER_USER_TO_MANAGER below (the same mapping used in sleeper-facts.js),
and manager_mapping.json is updated with whatever team_name each roster
is currently using, so generate_facts.py's existing team_name -> manager
lookup keeps working unchanged.

Usage:
    python export_sleeper_season.py
    python export_sleeper_season.py --dry-run   # print what would be written, don't touch files
"""

import argparse
import json
import time
from pathlib import Path

import requests

SLEEPER_LEAGUE_ID = "1392229432336347136"
SLEEPER_API = "https://api.sleeper.app/v1"
REQUEST_DELAY_SECONDS = 0.3

OUT_DIR = Path("data/history")
MAPPING_PATH = Path("manager_mapping.json")

# Sleeper user_id -> manager_id, matching managers.json / manager_mapping.json
# from the Yahoo-era history. Keep this in sync with the identical table in
# sleeper-facts.js if either ever changes (new manager, departure, etc.).
SLEEPER_USER_TO_MANAGER = {
    "684050990101504000": "nick",
    "684091770841165824": "john",
    "684101253923418112": "mark",
    "870464286609313792": "kodi",
    "884851377262829568": "mikey_g",
    "966417154155261952": "matt",
    "1002733712884359168": "owen",
    "1014914132291805184": "dennis",
    "1126229965009297408": "anthony",
    "1129123243807399936": "bob",
    "1390859945867476992": "bill_m",
    "1393763098669621248": "mikey_k",
}

EMPTY_PLAYOFF_GAME = {
    "week": None, "team1_id": None, "team2_id": None,
    "team1_score": 0, "team2_score": 0, "winner_id": None, "loser_id": None,
}


def sleeper_get(path: str):
    resp = requests.get(f"{SLEEPER_API}{path}", timeout=20)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return resp.json()


def resolve_completed_weeks(state: dict, league: dict) -> list[int]:
    """Mirrors resolveCompletedWeeks() in sleeper-facts.js -- keep both in
    sync if this logic ever changes. See that file for the reasoning on
    each season_type branch."""
    if state["season"] != league["season"]:
        return []
    if state["season_type"] == "pre":
        return []
    playoff_start = league["settings"].get("playoff_week_start") or 15
    if state["season_type"] == "post":
        current_week = playoff_start
    else:
        current_week = state["week"]
    last_completed = min(current_week - 1, playoff_start - 1)
    return list(range(1, last_completed + 1))


def build_season_json(league: dict, users: list[dict], rosters: list[dict], weeks: list[int]) -> tuple[dict, dict]:
    """Returns (season_json, new_mapping_entries)."""
    user_by_id = {u["user_id"]: u for u in users}

    team_name_by_roster = {}
    new_mapping_entries = {}
    for r in rosters:
        u = user_by_id.get(r["owner_id"], {})
        team_name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Roster {r['roster_id']}"
        team_name_by_roster[r["roster_id"]] = team_name
        mgr_id = SLEEPER_USER_TO_MANAGER.get(r["owner_id"])
        if mgr_id:
            new_mapping_entries[team_name] = mgr_id
        else:
            print(f"  !! WARNING: no manager mapping for Sleeper user {r['owner_id']} "
                  f"(team '{team_name}') -- generate_facts.py will fail on this team_name "
                  f"until SLEEPER_USER_TO_MANAGER is updated.")

    weekly_matchups = {}
    for w in weeks:
        weekly_matchups[w] = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/matchups/{w}")

    schedules = {r["roster_id"]: [] for r in rosters}
    for w in weeks:
        entries = weekly_matchups[w]
        by_matchup_id = {}
        for e in entries:
            by_matchup_id.setdefault(e["matchup_id"], []).append(e)
        for pair in by_matchup_id.values():
            if len(pair) != 2:
                continue  # bye or malformed -- skip rather than guess
            a, b = pair
            for mine, theirs in ((a, b), (b, a)):
                my_score = round(mine.get("points") or 0.0, 2)
                opp_score = round(theirs.get("points") or 0.0, 2)
                if my_score > opp_score:
                    result = "Win"
                elif my_score < opp_score:
                    result = "Loss"
                else:
                    result = "Tie"
                schedules[mine["roster_id"]].append({
                    "week": w,
                    "opponent_team_id": theirs["roster_id"],
                    "opponent_name": team_name_by_roster.get(theirs["roster_id"], f"Roster {theirs['roster_id']}"),
                    "my_score": my_score,
                    "opp_score": opp_score,
                    "result": result,
                })

    teams = {}
    for r in rosters:
        settings = r.get("settings") or {}
        points_for = (settings.get("fpts") or 0) + (settings.get("fpts_decimal") or 0) / 100
        points_against = (settings.get("fpts_against") or 0) + (settings.get("fpts_against_decimal") or 0) / 100
        teams[str(r["roster_id"])] = {
            "team_name": team_name_by_roster[r["roster_id"]],
            "wins": settings.get("wins", 0),
            "losses": settings.get("losses", 0),
            "ties": settings.get("ties", 0),
            "points_for": round(points_for, 2),
            "points_against": round(points_against, 2),
            "schedule": sorted(schedules[r["roster_id"]], key=lambda e: e["week"]),
        }

    playoff_week_start = league["settings"].get("playoff_week_start") or 15
    season_json = {
        "season": int(league["season"]),
        "regular_season_weeks": playoff_week_start - 1,
        "teams": teams,
        # TODO: real playoff bracket, once this league's playoffs actually happen --
        # see the module docstring. Placeholder here is intentional and is exactly
        # what generate_facts.py's is_season_complete() checks for.
        "playoffs": {
            "seeds": {},
            "quarterfinal": [],
            "semifinal": [],
            "fifth_place": [],
            "third_place": dict(EMPTY_PLAYOFF_GAME),
            "championship": dict(EMPTY_PLAYOFF_GAME),
        },
    }
    return season_json, new_mapping_entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't touch any files")
    args = parser.parse_args()

    print(f"Fetching league {SLEEPER_LEAGUE_ID}...")
    league = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}")
    users = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/users")
    rosters = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/rosters")
    state = sleeper_get("/state/nfl")

    weeks = resolve_completed_weeks(state, league)
    print(f"  season {league['season']}, season_type={state['season_type']}, "
          f"current week={state.get('week')} -> {len(weeks)} completed regular season week(s): {weeks}")

    season_json, new_mapping_entries = build_season_json(league, users, rosters, weeks)

    if args.dry_run:
        print("\n--- DRY RUN: would write the following season JSON ---")
        print(json.dumps(season_json, indent=2)[:2000], "...(truncated)" if len(json.dumps(season_json)) > 2000 else "")
        print("\n--- DRY RUN: would merge these entries into manager_mapping.json ---")
        print(json.dumps(new_mapping_entries, indent=2, ensure_ascii=False))
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{season_json['season']}.json"
    out_path.write_text(json.dumps(season_json, indent=2))
    print(f"  wrote {out_path}")

    mapping = json.loads(MAPPING_PATH.read_text()) if MAPPING_PATH.exists() else {}
    changed = False
    for team_name, mgr_id in new_mapping_entries.items():
        if mapping.get(team_name) != mgr_id:
            mapping[team_name] = mgr_id
            changed = True
    if changed:
        MAPPING_PATH.write_text(json.dumps(mapping, indent=2, ensure_ascii=False))
        print(f"  updated {MAPPING_PATH} with {len(new_mapping_entries)} current-season team name(s)")


if __name__ == "__main__":
    main()
