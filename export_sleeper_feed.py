"""
export_sleeper_feed.py

Generates data/feed.json: a rolling live feed of waiver moves, trades, and
in-progress matchup scores, meant to run frequently (every few hours) via
its own GitHub Actions workflow -- separate from update-facts.yml, which
stays on its existing Tuesday-only cadence. Transactions happen any day of
the week, so the feed needs its own, more frequent schedule.

Unlike export_sleeper_season.py, this script is stateless by design: every
run recomputes the whole feed from scratch (transactions since the season
started, plus one fresh matchup snapshot) and overwrites data/feed.json
wholesale. No "have we already logged this" bookkeeping to get wrong --
just re-derive the same answer every time from Sleeper's own data.

Same identity table as export_sleeper_season.py (SLEEPER_USER_TO_MANAGER)
-- keep both in sync if either ever changes.

Usage:
    python export_sleeper_feed.py
    python export_sleeper_feed.py --dry-run   # print what would be written, don't touch files
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SLEEPER_LEAGUE_ID = "1392229432336347136"
SLEEPER_API = "https://api.sleeper.app/v1"
REQUEST_DELAY_SECONDS = 0.3

OUT_PATH = Path("data/feed.json")
PLAYERS_CACHE_PATH = Path("data/.players_cache.json")
FEED_ITEM_LIMIT = 40  # trim to the most recent N items when writing

# Sleeper user_id -> manager_id. Copied from export_sleeper_season.py --
# keep in sync if either changes (new manager, departure, etc.).
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


def sleeper_get(path: str):
    resp = requests.get(f"{SLEEPER_API}{path}", timeout=20)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return resp.json()


def load_players(force_refresh: bool = False) -> dict:
    """Sleeper asks that /players/nfl not be hit more than once a day --
    it's a large, slow-changing dump. Cache locally; only refetch if the
    cache is missing or more than 24h old."""
    if not force_refresh and PLAYERS_CACHE_PATH.exists():
        cached = json.loads(PLAYERS_CACHE_PATH.read_text())
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age < 24 * 3600:
            return cached["players"]

    print("Fetching full player database (Sleeper asks this happen at most once/day)...")
    players = sleeper_get("/players/nfl")
    PLAYERS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYERS_CACHE_PATH.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }))
    return players


def resolve_relevant_weeks(state: dict, league: dict) -> tuple[list[int], int | None]:
    """Returns (completed_weeks, current_week_if_in_progress). Mirrors the
    resolveCompletedWeeks() logic in export_sleeper_season.py / sleeper-facts.js
    -- keep all three in sync if this reasoning ever changes."""
    if state["season"] != league["season"] or state["season_type"] == "pre":
        return [], None
    playoff_start = league["settings"].get("playoff_week_start") or 15
    current_week = playoff_start if state["season_type"] == "post" else state["week"]
    last_completed = min(current_week - 1, playoff_start - 1)
    completed = list(range(1, last_completed + 1))
    in_progress_week = current_week if current_week < playoff_start else None
    return completed, in_progress_week


def build_team_lookup(users: list, rosters: list) -> dict:
    """roster_id -> {"manager_id": ..., "team_name": ...}"""
    user_by_id = {u["user_id"]: u for u in users}
    lookup = {}
    for r in rosters:
        u = user_by_id.get(r["owner_id"], {})
        team_name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Roster {r['roster_id']}"
        lookup[r["roster_id"]] = {
            "manager_id": SLEEPER_USER_TO_MANAGER.get(r["owner_id"]),
            "team_name": team_name,
        }
    return lookup


def build_player_points(completed_weeks: list[int]) -> dict:
    """player_id -> [(week, points), ...] across every roster's matchup data
    for every completed week this season. Used for the depth-piece heuristic.
    Only covers players who were rostered by *someone* in this league at some
    point this season -- a player who's been a free agent all year simply has
    no entry here, and the depth heuristic skips rather than guessing."""
    points_by_player: dict[str, list[tuple[int, float]]] = {}
    for week in completed_weeks:
        matchups = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/matchups/{week}")
        for entry in matchups:
            for pid, pts in (entry.get("players_points") or {}).items():
                if pts is None:
                    continue
                points_by_player.setdefault(pid, []).append((week, pts))
    return points_by_player


def format_player(pid: str, players_db: dict) -> dict:
    meta = players_db.get(pid) or {}
    if meta.get("position") == "DEF":
        # Sleeper represents defenses by team abbreviation (e.g. "SF"), not a normal player_id
        name = meta.get("team") or pid
    else:
        name = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip() or pid
    return {"player_id": pid, "name": name, "position": meta.get("position")}


def ppg(pid: str, points_by_player: dict) -> float | None:
    history = points_by_player.get(pid)
    if not history:
        return None
    pts = [p for _, p in history]
    return sum(pts) / len(pts)


def get_depth_remark(added_pid: str, roster: dict, points_by_player: dict, players_db: dict) -> str | None:
    """v1 heuristic #1: compare the new add's season PPG (in-league games
    only) against the *current starters* at the same position on the roster
    that added them. If every current starter there already outscores the
    add, tag it a depth piece. Returns None -- no remark -- whenever there
    isn't enough data to make the call honestly; this is meant to be
    conservative, not clever."""
    position = (players_db.get(added_pid) or {}).get("position")
    if position not in ("QB", "RB", "WR", "TE", "K", "DEF"):
        return None

    added_ppg = ppg(added_pid, points_by_player)
    if added_ppg is None:
        return None  # no in-league points history for this player -- skip, don't guess

    starter_ppgs = []
    for pid in (roster.get("starters") or []):
        if pid in ("0", None):
            continue
        if (players_db.get(pid) or {}).get("position") != position:
            continue
        p = ppg(pid, points_by_player)
        if p is not None:
            starter_ppgs.append(p)

    if not starter_ppgs:
        return None  # no comparable current starters at this position -- skip

    return "depth" if all(p > added_ppg for p in starter_ppgs) else None


def get_streamer_remark(position: str | None, prior_moves_at_position: int) -> str | None:
    """v1 heuristic #2, K/DEF only: purely behavioral, no matchup or points
    data needed at all. If this manager has made 2+ *prior* add/drops at this
    same position slot already this season, tag it a streamer. K/DEF churn
    is the classic streaming signal, and this is fully self-contained from
    the transaction log itself -- the safest heuristic to start with.
    Extending this to skill positions (bye-week timing, matchup strength) is
    a reasonable v2 addition once this is proven out, not part of v1."""
    if position not in ("K", "DEF"):
        return None
    return "streamer" if prior_moves_at_position >= 2 else None


def fetch_all_transactions(weeks: list[int]) -> list[dict]:
    all_tx = []
    seen_ids = set()
    for week in weeks:
        for tx in sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/transactions/{week}"):
            if tx.get("status") != "complete":
                continue
            if tx["transaction_id"] in seen_ids:
                continue
            seen_ids.add(tx["transaction_id"])
            all_tx.append(tx)
    all_tx.sort(key=lambda t: t.get("status_updated") or t.get("created") or 0)
    return all_tx


def build_feed_items(transactions: list[dict], team_lookup: dict, rosters_by_id: dict,
                      players_db: dict, points_by_player: dict) -> list[dict]:
    items = []
    # running count of prior waiver/free-agent adds per (roster_id, position),
    # built up as we walk transactions in chronological order -- this is what
    # the streamer heuristic checks against for each new add.
    position_move_counts: dict[tuple, int] = {}

    for tx in transactions:
        timestamp = tx.get("status_updated") or tx.get("created")

        if tx["type"] == "trade":
            sides = []
            adds, drops = tx.get("adds") or {}, tx.get("drops") or {}
            waiver_budget = tx.get("waiver_budget") or []
            for roster_id in tx.get("roster_ids", []):
                gave = [format_player(pid, players_db) for pid, rid in drops.items() if rid == roster_id]
                got = [format_player(pid, players_db) for pid, rid in adds.items() if rid == roster_id]
                faab_moves = [
                    {"amount": wb["amount"], "to": team_lookup.get(wb["receiver"], {}).get("team_name")}
                    for wb in waiver_budget if wb.get("sender") == roster_id
                ]
                sides.append({**team_lookup.get(roster_id, {}), "gave": gave, "got": got, "faab_sent": faab_moves})
            items.append({
                "type": "trade",
                "transaction_id": tx["transaction_id"],
                "timestamp": timestamp,
                "sides": sides,
            })
            continue

        # waiver / free_agent moves -- one feed item per player added,
        # paired with a same-roster drop if one exists in this transaction
        adds = tx.get("adds") or {}
        drops = dict(tx.get("drops") or {})
        faab = (tx.get("settings") or {}).get("waiver_bid")

        for pid, roster_id in adds.items():
            added_meta = format_player(pid, players_db)
            position = added_meta["position"]

            dropped_meta = None
            for dpid, drid in list(drops.items()):
                if drid == roster_id:
                    dropped_meta = format_player(dpid, players_db)
                    del drops[dpid]
                    break

            roster = rosters_by_id.get(roster_id, {})
            remark = get_depth_remark(pid, roster, points_by_player, players_db)
            if remark is None:
                key = (roster_id, position)
                remark = get_streamer_remark(position, position_move_counts.get(key, 0))
                position_move_counts[key] = position_move_counts.get(key, 0) + 1

            items.append({
                "type": "waiver_move",
                "transaction_id": tx["transaction_id"],
                "timestamp": timestamp,
                **team_lookup.get(roster_id, {}),
                "added": added_meta,
                "dropped": dropped_meta,
                "faab": faab,
                "remark": remark,
            })

    return items


def build_matchup_inprogress_item(in_progress_week: int | None, team_lookup: dict) -> dict | None:
    """A single fresh snapshot of any matchup with a non-zero score so far
    this week -- e.g. useful Friday morning after Thursday Night Football.
    Not accumulated across runs; each run's snapshot simply replaces the
    last one, since a stale in-progress score is actively misleading."""
    if in_progress_week is None:
        return None

    matchups = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/matchups/{in_progress_week}")
    by_matchup_id: dict = {}
    for entry in matchups:
        by_matchup_id.setdefault(entry["matchup_id"], []).append(entry)

    pairs = []
    for pair in by_matchup_id.values():
        if len(pair) != 2:
            continue
        a, b = pair
        if (a.get("points") or 0) == 0 and (b.get("points") or 0) == 0:
            continue  # nobody's played yet -- nothing worth mentioning
        pairs.append({
            "team": team_lookup.get(a["roster_id"], {}).get("team_name"),
            "score": a.get("points") or 0,
            "opponent": team_lookup.get(b["roster_id"], {}).get("team_name"),
            "opponent_score": b.get("points") or 0,
        })

    if not pairs:
        return None

    return {"type": "matchup_inprogress", "week": in_progress_week, "matchups": pairs}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't touch any files")
    args = parser.parse_args()

    print(f"Fetching league {SLEEPER_LEAGUE_ID}...")
    league = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}")
    users = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/users")
    rosters = sleeper_get(f"/league/{SLEEPER_LEAGUE_ID}/rosters")
    state = sleeper_get("/state/nfl")
    rosters_by_id = {r["roster_id"]: r for r in rosters}

    completed_weeks, in_progress_week = resolve_relevant_weeks(state, league)
    print(f"  completed weeks: {completed_weeks}, in-progress week: {in_progress_week}")

    players_db = load_players()
    team_lookup = build_team_lookup(users, rosters)

    print("  building in-league player points history (for the depth heuristic)...")
    points_by_player = build_player_points(completed_weeks)

    tx_weeks = completed_weeks + ([in_progress_week] if in_progress_week else [])
    print(f"  fetching transactions for weeks: {tx_weeks}")
    transactions = fetch_all_transactions(tx_weeks)
    print(f"  {len(transactions)} completed transactions found")

    items = build_feed_items(transactions, team_lookup, rosters_by_id, players_db, points_by_player)

    matchup_item = build_matchup_inprogress_item(in_progress_week, team_lookup)
    if matchup_item:
        items.append(matchup_item)

    # newest first, trimmed to the most recent N
    items.sort(key=lambda i: i.get("timestamp") or 0, reverse=True)
    items = items[:FEED_ITEM_LIMIT]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }

    if args.dry_run:
        print("\n--- DRY RUN: would write the following feed ---")
        print(json.dumps(output, indent=2)[:3000], "...(truncated)" if len(json.dumps(output)) > 3000 else "")
        return

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  wrote {OUT_PATH} ({len(items)} items)")


if __name__ == "__main__":
    main()
