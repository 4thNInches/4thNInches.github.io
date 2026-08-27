"""
generate_facts.py

Turns the season-by-season history JSONs + manager_mapping.json into one big
facts.json: a flat list of standalone, ready-to-display trivia facts for a
random ticker on a static site.

Input layout expected (override with CLI flags if yours differs):
    data/history/<year>.json    -- one per season, from export_yahoo_history.py
    manager_mapping.json        -- team_name -> manager_id
    managers.json               -- manager_id -> {display_name, seasons_played, team_names_used}

Output:
    facts.json -- list of:
        {
          "id": "highest_score_0001",
          "category": "highest_score",
          "text": "...",              <- ready to show as-is
          "value": 187.44,            <- primary metric, for sorting/filtering
          "season": 2022,             <- or null if not game-specific
          "week": 14,                 <- or null
          "round": "championship",    <- regular_season | quarterfinal | semifinal
                                          | fifth_place | championship | third_place | null
          "managers": ["mark", "nick"], <- manager_ids involved, or []
        }

VOICE NOTES (v2): wording is calibrated off real 4th & Inches recap language.
Two rules drive every template below:
  1. A team is identified as "Team Name (Manager)" -- team names are the
     specific, real identity for that season and read more naturally, but
     since names change manager to manager (and manager to manager) year to
     year, the manager tag is what keeps a standalone fact unambiguous.
     Career-spanning facts (head-to-head, career totals) use manager name
     alone, since no single team name applies across years.
  2. Each category has 2-3 alternate sentence shapes, picked at random
     (seeded, so re-running the generator on unchanged data reproduces the
     same wording) so 800+ facts don't all read like a mail merge.
No team-name wordplay/nicknames are attempted -- that needs input on what
each name's "bit" actually is, which isn't derivable from the data alone.

Usage:
    python generate_facts.py
    python generate_facts.py --data-dir data/history --out facts.json
"""

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

def discover_years(data_dir: Path) -> list[int]:
    """Every season this can see is whatever <year>.json files actually
    exist in data_dir -- not a hardcoded range that needs bumping by hand
    every season. Matches files named exactly e.g. '2026.json'."""
    years = []
    for p in data_dir.glob("*.json"):
        if p.stem.isdigit() and len(p.stem) == 4:
            years.append(int(p.stem))
    return sorted(years)

ROUND_LABEL = {
    "regular_season": "the regular season",
    "quarterfinal": "the quarterfinals",
    "semifinal": "the semifinals",
    "fifth_place": "the 5th place game",
    "championship": "the championship",
    "third_place": "the 3rd place game",
}

# Fixed seed: phrasing choices are reproducible across re-runs on unchanged
# data, so regenerating doesn't churn every line in a git diff.
random.seed(20260825)


def normalize_seeds(seeds_raw: dict) -> dict:
    """Returns {team_id (int): seed (int)}, regardless of which orientation
    the source file uses:
      - old export format: {seed_str: team_id}
      - newer export format: {team_id_str: seed}
    Detected from the data itself, not assumed: a playoff bracket always has
    seeds exactly {1,2,3,4,5,6}, so whichever side of the dict sorts to
    exactly [1,2,3,4,5,6] is the seed side. This is more reliable than
    checking key size, since team_ids could coincidentally also fall in
    1-6 in a given season. An empty dict (in-progress season, playoffs
    haven't happened yet) returns empty, not an error."""
    if not seeds_raw:
        return {}
    keys = [int(k) for k in seeds_raw.keys()]
    vals = list(seeds_raw.values())
    if sorted(vals) == [1, 2, 3, 4, 5, 6]:
        return {int(k): v for k, v in seeds_raw.items()}  # already team_id -> seed
    elif sorted(keys) == [1, 2, 3, 4, 5, 6]:
        return {v: int(k) for k, v in seeds_raw.items()}  # seed -> team_id, invert it
    else:
        raise ValueError(f"Can't determine seeds orientation from {seeds_raw}")


def playoff_rounds(po: dict) -> dict:
    """Returns the four/five bracket rounds under consistent singular keys,
    accepting either the old plural key names (quarterfinals/semifinals) or
    the newer singular ones (quarterfinal/semifinal). fifth_place is new
    and simply absent (treated as empty) in older files."""
    return {
        "quarterfinal": po.get("quarterfinal", po.get("quarterfinals", [])),
        "semifinal": po.get("semifinal", po.get("semifinals", [])),
        "fifth_place": po.get("fifth_place", []),
        "championship": po["championship"],
        "third_place": po["third_place"],
    }


def pick(options: list[str]) -> str:
    return random.choice(options)


def ordinal_word(n: int, noun: str) -> str:
    """'highest' / '2nd-highest' etc, generic over noun direction (highest/lowest/biggest/closest)."""
    words = {1: noun, 2: f"2nd-{noun}", 3: f"3rd-{noun}", 4: f"4th-{noun}", 5: f"5th-{noun}"}
    return words.get(n, f"{n}th-{noun}")


def bare_ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th', 12 -> '12th' -- for phrasing like
    'the 10th-most points scored' where ordinal_word's noun-suffix isn't a fit."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# loading + normalizing
# ---------------------------------------------------------------------------

def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_all(data_dir: Path, mapping_path: Path, managers_path: Path):
    years = discover_years(data_dir)
    seasons = {y: load_json(data_dir / f"{y}.json") for y in years}
    name_to_mgr = load_json(mapping_path)
    managers = load_json(managers_path)
    return seasons, name_to_mgr, managers, years


def build_games(seasons: dict, name_to_mgr: dict) -> list[dict]:
    """One row per game (deduped), regular season + all playoff rounds, with
    manager ids attached to both sides."""
    games = []
    for year, d in seasons.items():
        teams = d["teams"]

        def name(tid):
            return teams[str(tid)]["team_name"]

        def mgr(tid):
            return name_to_mgr[name(tid)]

        seen = set()
        for tid_str, t in teams.items():
            tid = int(tid_str)
            for entry in t["schedule"]:
                opp = entry["opponent_team_id"]
                if opp is None:
                    continue
                key = (year, entry["week"], min(tid, opp), max(tid, opp))
                if key in seen:
                    continue
                seen.add(key)
                games.append(dict(
                    season=year, week=entry["week"], round="regular_season",
                    team1_id=tid, team1_name=name(tid), team1_mgr=mgr(tid), team1_score=entry["my_score"],
                    team2_id=opp, team2_name=name(opp), team2_mgr=mgr(opp), team2_score=entry["opp_score"],
                ))

        po = d["playoffs"]
        rounds_data = playoff_rounds(po)
        rounds = (
            [("quarterfinal", m) for m in rounds_data["quarterfinal"]]
            + [("semifinal", m) for m in rounds_data["semifinal"]]
            + [("fifth_place", m) for m in rounds_data["fifth_place"]]
            + [("championship", rounds_data["championship"]), ("third_place", rounds_data["third_place"])]
        )
        for round_name, m in rounds:
            if m.get("winner_id") is None:
                continue  # not played yet -- in-progress season, not a real game
            t1, t2 = m["team1_id"], m["team2_id"]
            games.append(dict(
                season=year, week=m["week"], round=round_name,
                team1_id=t1, team1_name=name(t1), team1_mgr=mgr(t1), team1_score=m["team1_score"],
                team2_id=t2, team2_name=name(t2), team2_mgr=mgr(t2), team2_score=m["team2_score"],
            ))

    for g in games:
        g["margin"] = round(abs(g["team1_score"] - g["team2_score"]), 2)
        g["combined"] = round(g["team1_score"] + g["team2_score"], 2)
        g["is_tie"] = g["team1_score"] == g["team2_score"]
        if g["is_tie"]:
            g["winner_mgr"] = g["loser_mgr"] = None
        elif g["team1_score"] > g["team2_score"]:
            g["winner_mgr"], g["loser_mgr"] = g["team1_mgr"], g["team2_mgr"]
        else:
            g["winner_mgr"], g["loser_mgr"] = g["team2_mgr"], g["team1_mgr"]

    return games


def build_team_seasons(seasons: dict, name_to_mgr: dict) -> list[dict]:
    rows = []
    for year, d in seasons.items():
        for tid_str, t in d["teams"].items():
            games_played = t["wins"] + t["losses"] + t["ties"]
            win_pct = (t["wins"] + 0.5 * t["ties"]) / games_played if games_played else 0.0
            rows.append(dict(
                season=year, team_id=int(tid_str), team_name=t["team_name"],
                mgr=name_to_mgr[t["team_name"]],
                wins=t["wins"], losses=t["losses"], ties=t["ties"],
                points_for=t["points_for"], points_against=t["points_against"],
                games_played=games_played, win_pct=win_pct,
                schedule=t["schedule"],
            ))
    return rows


# ---------------------------------------------------------------------------
# fact collector
# ---------------------------------------------------------------------------

class FactBook:
    def __init__(self):
        self.facts = []
        self._counter = defaultdict(int)

    def add(self, category, text, value=None, season=None, week=None, round=None, managers=None):
        self._counter[category] += 1
        self.facts.append({
            "id": f"{category}_{self._counter[category]:04d}",
            "category": category,
            "text": text,
            "value": value,
            "season": season,
            "week": week,
            "round": round,
            "managers": sorted(set(managers)) if managers else [],
        })


def game_desc(g):
    return f"{ROUND_LABEL[g['round']]} of {g['season']}, Week {g['week']}"


def game_desc_cap(g):
    """Capitalized game_desc, for templates where it opens the sentence."""
    d = game_desc(g)
    return d[0].upper() + d[1:]


def meetings_word(n: int) -> str:
    return "meeting" if n == 1 else "meetings"


def is_season_complete(season_data: dict) -> bool:
    """A season counts as complete once its championship has been decided.
    Used to keep an in-progress season's partial totals out of rankings
    where comparing partial-vs-complete would be misleading -- e.g. 5
    weeks of points looks like a historic low next to a full 14-week
    season, but it isn't a fair comparison. Win-percentage-based rankings
    don't need this (already normalized for games played); this only
    matters for raw totals and season-to-season comparisons."""
    champ = season_data.get("playoffs", {}).get("championship", {})
    return champ.get("winner_id") is not None


def team_ref(team_name: str, mgr_id: str, managers: dict) -> str:
    """'Team Name (Manager)' -- the disambiguation convention: team names
    are the real, season-specific identity, but since the same manager's
    team name changes across years (and different managers have reused
    similar names), the manager tag is what keeps a standalone fact
    unambiguous without the surrounding season-long narrative a recap has.
    Skipped when the manager's own name is already a word in the team name
    (e.g. "Newman's Owen" run by Owen) -- the tag would just be redundant."""
    display = managers[mgr_id]["display_name"]
    if re.search(rf"\b{re.escape(display)}\b", team_name, re.IGNORECASE):
        return team_name
    return f"{team_name} ({display})"


# ---------------------------------------------------------------------------
# category builders
# ---------------------------------------------------------------------------

def single_game_records(fb: FactBook, games: list[dict], managers: dict, top_n=5):
    def ref(g, side):
        return team_ref(g[f"{side}_name"], g[f"{side}_mgr"], managers)

    # highest / lowest single-team score
    entries = []
    for g in games:
        entries.append((g["team1_score"], g, "team1"))
        entries.append((g["team2_score"], g, "team2"))

    high_verbs = ["dropped", "put up", "racked up", "posted"]
    for score, g, side in sorted(entries, key=lambda x: -x[0])[:top_n]:
        rank = len([e for e in sorted(entries, key=lambda x: -x[0])[:top_n] if e[0] > score]) + 1
        opp_side = "team2" if side == "team1" else "team1"
        this_ref, opp_ref = ref(g, side), ref(g, opp_side)
        opp_score = g[f"{opp_side}_score"]
        ordinal = ordinal_word(rank, "highest")
        text = pick([
            f"{this_ref} {pick(high_verbs)} {score:.2f} points in {game_desc(g)} against {opp_ref} "
            f"({opp_score:.2f}) \u2014 the {ordinal} single-game score in league history.",
            f"{this_ref}'s {score:.2f}-point performance in {game_desc(g)} against {opp_ref} "
            f"({opp_score:.2f}) ranks {ordinal} all-time.",
            f"In {game_desc(g)}, {this_ref} put {score:.2f} points on the board against {opp_ref} "
            f"({opp_score:.2f}) \u2014 good for the {ordinal} single-game total in league history.",
        ])
        fb.add("highest_score", text, value=score, season=g["season"], week=g["week"], round=g["round"],
               managers=[g[f"{side}_mgr"], g[f"{opp_side}_mgr"]])

    low_verbs = ["could only muster", "managed just", "scraped together"]
    for score, g, side in sorted(entries, key=lambda x: x[0])[:top_n]:
        rank = len([e for e in sorted(entries, key=lambda x: x[0])[:top_n] if e[0] < score]) + 1
        opp_side = "team2" if side == "team1" else "team1"
        this_ref, opp_ref = ref(g, side), ref(g, opp_side)
        opp_score = g[f"{opp_side}_score"]
        ordinal = ordinal_word(rank, "lowest")
        text = pick([
            f"{this_ref} {pick(low_verbs)} {score:.2f} points in {game_desc(g)} against {opp_ref} "
            f"({opp_score:.2f}) \u2014 the {ordinal} single-game score in league history.",
            f"{this_ref}'s {score:.2f}-point dud in {game_desc(g)} against {opp_ref} ({opp_score:.2f}) "
            f"ranks {ordinal} all-time \u2014 from the bottom.",
            f"In {game_desc(g)}, {this_ref} managed just {score:.2f} points against {opp_ref} "
            f"({opp_score:.2f}), the {ordinal} single-game total in league history.",
        ])
        fb.add("lowest_score", text, value=score, season=g["season"], week=g["week"], round=g["round"],
               managers=[g[f"{side}_mgr"], g[f"{opp_side}_mgr"]])

    # biggest blowout / closest margin (decisive games only)
    decisive = [g for g in games if not g["is_tie"]]
    for i, g in enumerate(sorted(decisive, key=lambda x: -x["margin"])[:top_n], start=1):
        win_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        lose_side = "team2" if win_side == "team1" else "team1"
        w_ref, l_ref = ref(g, win_side), ref(g, lose_side)
        ws, ls = g[f"{win_side}_score"], g[f"{lose_side}_score"]
        ordinal = ordinal_word(i, "biggest")
        text = pick([
            f"{w_ref} demolished {l_ref} {ws:.2f}-{ls:.2f} in {game_desc(g)}, a {g['margin']:.2f}-point "
            f"margin \u2014 the {ordinal} blowout in league history.",
            f"{game_desc_cap(g)} wasn't close: {w_ref} beat {l_ref} {ws:.2f}-{ls:.2f}, a {g['margin']:.2f}"
            f"-point margin that ranks {ordinal} all-time.",
            f"{w_ref} put {g['margin']:.2f} points between themselves and {l_ref} ({ws:.2f}-{ls:.2f}) "
            f"in {game_desc(g)} \u2014 the {ordinal} margin in league history.",
        ])
        if i == 1:
            text += " Nothing else comes close."
        fb.add("biggest_blowout", text, value=g["margin"], season=g["season"], week=g["week"], round=g["round"],
               managers=[g["winner_mgr"], g["loser_mgr"]])

    for i, g in enumerate(sorted(decisive, key=lambda x: x["margin"])[:top_n], start=1):
        win_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        lose_side = "team2" if win_side == "team1" else "team1"
        w_ref, l_ref = ref(g, win_side), ref(g, lose_side)
        ws, ls = g[f"{win_side}_score"], g[f"{lose_side}_score"]
        ordinal = ordinal_word(i, "closest")
        text = pick([
            f"{w_ref} narrowly outlasted {l_ref} {ws:.2f}-{ls:.2f} in {game_desc(g)}, a margin of just "
            f"{g['margin']:.2f} points \u2014 the {ordinal} game in league history.",
            f"{g['margin']:.2f} points. That's all that separated {w_ref} from {l_ref} in {game_desc(g)} "
            f"({ws:.2f}-{ls:.2f}) \u2014 the {ordinal} finish on record.",
            f"{game_desc_cap(g)} came down to the wire: {w_ref} edged {l_ref} {ws:.2f}-{ls:.2f}, a "
            f"{g['margin']:.2f}-point margin that ranks {ordinal} all-time.",
        ])
        if i == 1:
            text += " As close as it gets."
        fb.add("closest_margin", text, value=g["margin"], season=g["season"], week=g["week"], round=g["round"],
               managers=[g["winner_mgr"], g["loser_mgr"]])

    # highest / lowest combined score
    for i, g in enumerate(sorted(games, key=lambda x: -x["combined"])[:top_n], start=1):
        t1_ref, t2_ref = ref(g, "team1"), ref(g, "team2")
        ordinal = ordinal_word(i, "highest")
        text = pick([
            f"{t1_ref} and {t2_ref} combined for {g['combined']:.2f} points ({g['team1_score']:.2f}-"
            f"{g['team2_score']:.2f}) in {game_desc(g)} \u2014 the {ordinal} combined score in league history.",
            f"{game_desc_cap(g)} produced {g['combined']:.2f} combined points between {t1_ref} and {t2_ref} "
            f"({g['team1_score']:.2f}-{g['team2_score']:.2f}) \u2014 {ordinal} all-time.",
        ])
        fb.add("highest_combined", text, value=g["combined"], season=g["season"], week=g["week"], round=g["round"],
               managers=[g["team1_mgr"], g["team2_mgr"]])

    for i, g in enumerate(sorted(games, key=lambda x: x["combined"])[:top_n], start=1):
        t1_ref, t2_ref = ref(g, "team1"), ref(g, "team2")
        ordinal = ordinal_word(i, "lowest")
        text = pick([
            f"{t1_ref} and {t2_ref} combined for just {g['combined']:.2f} points ({g['team1_score']:.2f}-"
            f"{g['team2_score']:.2f}) in {game_desc(g)} \u2014 the {ordinal} combined score in league history.",
            f"{game_desc_cap(g)} was a slog: {t1_ref} and {t2_ref} combined for only {g['combined']:.2f} "
            f"points ({g['team1_score']:.2f}-{g['team2_score']:.2f}) \u2014 {ordinal} all-time.",
        ])
        fb.add("lowest_combined", text, value=g["combined"], season=g["season"], week=g["week"], round=g["round"],
               managers=[g["team1_mgr"], g["team2_mgr"]])

    # ties -- every instance, however rare
    for g in games:
        if g["is_tie"]:
            t1_ref, t2_ref = ref(g, "team1"), ref(g, "team2")
            fb.add("tie_game",
                   f"{t1_ref} and {t2_ref} tied {g['team1_score']:.2f}-{g['team2_score']:.2f} in "
                   f"{game_desc(g)} \u2014 one of the rare ties in league history.",
                   value=g["team1_score"], season=g["season"], week=g["week"], round=g["round"],
                   managers=[g["team1_mgr"], g["team2_mgr"]])

    # ugliest win -- lowest score that still resulted in a win. Distinct from
    # lowest_score above, which doesn't care whether the score won or lost.
    winning_scores = []
    for g in games:
        if g["is_tie"]:
            continue
        winner_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        loser_side = "team2" if winner_side == "team1" else "team1"
        winning_scores.append((g[f"{winner_side}_score"], g, winner_side, loser_side))

    for i, (score, g, winner_side, loser_side) in enumerate(sorted(winning_scores, key=lambda x: x[0])[:top_n], start=1):
        w_ref, l_ref = ref(g, winner_side), ref(g, loser_side)
        opp_score = g[f"{loser_side}_score"]
        ordinal = ordinal_word(i, "ugliest")
        fb.add("ugliest_win",
               f"{w_ref} won {game_desc(g)} with just {score:.2f} points against {l_ref} ({opp_score:.2f}) "
               f"\u2014 the {ordinal} winning score in league history.",
               value=score, season=g["season"], week=g["week"], round=g["round"],
               managers=[g[f"{winner_side}_mgr"], g[f"{loser_side}_mgr"]])


def season_records(fb: FactBook, team_seasons: list[dict], managers: dict, complete_seasons: set, top_n=5):
    def ppg(ts):
        return ts["points_for"] / ts["games_played"] if ts["games_played"] else 0.0

    # win-percentage-based rankings are already normalized for games played,
    # so an in-progress season competes fairly here -- no filtering needed.
    by_win_pct = sorted(team_seasons, key=lambda t: (-t["win_pct"], -t["points_for"]))
    for i, ts in enumerate(by_win_pct[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "best")
        text = pick([
            f"{r} posted a {ts['wins']}-{ts['losses']}-{ts['ties']} record in {ts['season']} "
            f"({ts['points_for']:.2f} points, {ppg(ts):.2f} PPG) \u2014 the {ordinal} single-season record "
            f"in league history.",
            f"In {ts['season']}, {r} went {ts['wins']}-{ts['losses']}-{ts['ties']} \u2014 the {ordinal} "
            f"regular-season mark this league has ever seen.",
        ])
        fb.add("best_season_record", text, value=ts["win_pct"], season=ts["season"], managers=[ts["mgr"]])

    for i, ts in enumerate(sorted(by_win_pct, key=lambda t: (t["win_pct"], t["points_for"]))[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "worst")
        text = pick([
            f"{r} posted a {ts['wins']}-{ts['losses']}-{ts['ties']} record in {ts['season']} "
            f"({ts['points_for']:.2f} points, {ppg(ts):.2f} PPG) \u2014 the {ordinal} single-season record "
            f"in league history.",
            f"{ts['season']} was rough for {r}: a {ts['wins']}-{ts['losses']}-{ts['ties']} finish, the "
            f"{ordinal} regular-season mark this league has ever produced.",
        ])
        fb.add("worst_season_record", text, value=ts["win_pct"], season=ts["season"], managers=[ts["mgr"]])

    # raw point TOTALS, unlike win pct, aren't normalized for games played --
    # an in-progress season's partial total would otherwise look like a
    # historic extreme just for not being finished yet. Complete seasons only.
    complete_rows = [ts for ts in team_seasons if ts["season"] in complete_seasons]

    for i, ts in enumerate(sorted(complete_rows, key=lambda t: -t["points_for"])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "highest")
        text = pick([
            f"{r} scored {ts['points_for']:.2f} points ({ppg(ts):.2f} PPG) across the {ts['season']} regular "
            f"season \u2014 the {ordinal} single-season point total in league history.",
            f"No small feat: {r} put up {ts['points_for']:.2f} points in {ts['season']} "
            f"({ts['wins']}-{ts['losses']}-{ts['ties']}), {ordinal} all-time for a single season.",
        ])
        fb.add("most_points_season", text, value=ts["points_for"], season=ts["season"], managers=[ts["mgr"]])

    for i, ts in enumerate(sorted(complete_rows, key=lambda t: t["points_for"])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "lowest")
        text = pick([
            f"{r} scored just {ts['points_for']:.2f} points ({ppg(ts):.2f} PPG) across the {ts['season']} "
            f"regular season \u2014 the {ordinal} single-season point total in league history.",
            f"{ts['season']} was a scoring drought for {r}: {ts['points_for']:.2f} points "
            f"({ts['wins']}-{ts['losses']}-{ts['ties']}), {ordinal} all-time for a single season.",
        ])
        fb.add("fewest_points_season", text, value=ts["points_for"], season=ts["season"], managers=[ts["mgr"]])

    for i, ts in enumerate(sorted(complete_rows, key=lambda t: t["points_against"])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "fewest")
        text = (f"{r} allowed just {ts['points_against']:.2f} points across the {ts['season']} regular season "
                f"\u2014 the {ordinal} points allowed by any team in a single season.")
        fb.add("stingiest_schedule", text, value=ts["points_against"], season=ts["season"], managers=[ts["mgr"]])

    for i, ts in enumerate(sorted(complete_rows, key=lambda t: -t["points_against"])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "most")
        text = (f"{r} had {ts['points_against']:.2f} points scored against them in {ts['season']} \u2014 the "
                f"{ordinal} points allowed by any team in a single season.")
        fb.add("toughest_schedule", text, value=ts["points_against"], season=ts["season"], managers=[ts["mgr"]])

    # in-season streaks
    streak_rows = []
    for ts in team_seasons:
        sched = sorted(ts["schedule"], key=lambda e: e["week"])
        results = [e["result"] for e in sched]

        def longest(target):
            best = cur = 0
            for r in results:
                if r == target:
                    cur += 1
                    best = max(best, cur)
                else:
                    cur = 0
            return best

        streak_rows.append((ts, longest("Win"), longest("Loss")))

    for i, (ts, win_streak, _) in enumerate(sorted(streak_rows, key=lambda x: -x[1])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "longest")
        text = pick([
            f"{r} won {win_streak} straight games during the {ts['season']} regular season \u2014 the "
            f"{ordinal} in-season win streak in league history.",
            f"{r} ran off {win_streak} consecutive wins in {ts['season']}, {ordinal} single-season streak "
            f"on record.",
        ])
        fb.add("season_win_streak", text, value=win_streak, season=ts["season"], managers=[ts["mgr"]])

    for i, (ts, _, loss_streak) in enumerate(sorted(streak_rows, key=lambda x: -x[2])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "longest")
        text = pick([
            f"{r} lost {loss_streak} straight games during the {ts['season']} regular season \u2014 the "
            f"{ordinal} in-season losing streak in league history.",
            f"{r} dropped {loss_streak} in a row during {ts['season']}, {ordinal} single-season skid on record.",
        ])
        fb.add("season_loss_streak", text, value=loss_streak, season=ts["season"], managers=[ts["mgr"]])


def playoff_facts(fb: FactBook, seasons: dict, games: list[dict], managers: dict, name_to_mgr: dict, top_n=3):
    for year in sorted(seasons.keys()):
        d = seasons[year]
        po = d["playoffs"]
        teams = d["teams"]

        def name(tid):
            return teams[str(tid)]["team_name"]

        def mgr(tid):
            return name_to_mgr[name(tid)]

        champ = po["championship"]
        w, l = champ["winner_id"], champ["loser_id"]
        if w is not None:
            wscore = champ["team1_score"] if champ["team1_id"] == w else champ["team2_score"]
            lscore = champ["team1_score"] if champ["team1_id"] == l else champ["team2_score"]
            w_ref, l_ref = team_ref(name(w), mgr(w), managers), team_ref(name(l), mgr(l), managers)
            fb.add("championship_history",
                   f"{year} Champion: {w_ref} beat {l_ref} {wscore:.2f}-{lscore:.2f}.",
                   value=year, season=year, week=champ["week"], round="championship",
                   managers=[mgr(w), mgr(l)])

        third = po["third_place"]
        w3, l3 = third["winner_id"], third["loser_id"]
        if w3 is not None:
            wscore = third["team1_score"] if third["team1_id"] == w3 else third["team2_score"]
            lscore = third["team1_score"] if third["team1_id"] == l3 else third["team2_score"]
            w_ref, l_ref = team_ref(name(w3), mgr(w3), managers), team_ref(name(l3), mgr(l3), managers)
            fb.add("third_place_history",
                   f"{year} 3rd Place: {w_ref} beat {l_ref} {wscore:.2f}-{lscore:.2f}.",
                   value=year, season=year, week=third["week"], round="third_place",
                   managers=[mgr(w3), mgr(l3)])

        fifth_games = playoff_rounds(po)["fifth_place"]  # absent entirely in older-format seasons
        if fifth_games:
            fifth = fifth_games[0]
            w5, l5 = fifth["winner_id"], fifth["loser_id"]
            if w5 is not None:
                wscore = fifth["team1_score"] if fifth["team1_id"] == w5 else fifth["team2_score"]
                lscore = fifth["team1_score"] if fifth["team1_id"] == l5 else fifth["team2_score"]
                w_ref, l_ref = team_ref(name(w5), mgr(w5), managers), team_ref(name(l5), mgr(l5), managers)
                fb.add("fifth_place_history",
                       f"{year} 5th Place: {w_ref} beat {l_ref} {wscore:.2f}-{lscore:.2f}.",
                       value=year, season=year, week=fifth["week"], round="fifth_place",
                       managers=[mgr(w5), mgr(l5)])

    # highest scoring playoff games
    playoff_games = [g for g in games if g["round"] != "regular_season"]
    for i, g in enumerate(sorted(playoff_games, key=lambda x: -x["combined"])[:top_n], start=1):
        t1_ref = team_ref(g["team1_name"], g["team1_mgr"], managers)
        t2_ref = team_ref(g["team2_name"], g["team2_mgr"], managers)
        ordinal = ordinal_word(i, "highest")
        fb.add("highest_scoring_playoff_game",
               f"{t1_ref} and {t2_ref} combined for {g['combined']:.2f} points in {game_desc(g)} \u2014 "
               f"the {ordinal}-scoring playoff game in league history.",
               value=g["combined"], season=g["season"], week=g["week"], round=g["round"],
               managers=[g["team1_mgr"], g["team2_mgr"]])

    # biggest playoff upsets, by seed differential
    upsets = []
    for year, d in seasons.items():
        seeds = normalize_seeds(d["playoffs"]["seeds"])  # team_id -> seed
        for g in games:
            if g["season"] != year or g["round"] == "regular_season":
                continue
            s1, s2 = seeds.get(g["team1_id"]), seeds.get(g["team2_id"])
            if s1 is None or s2 is None or g["is_tie"]:
                continue
            winner_id = g["team1_id"] if g["team1_score"] > g["team2_score"] else g["team2_id"]
            loser_id = g["team2_id"] if winner_id == g["team1_id"] else g["team1_id"]
            winner_seed, loser_seed = seeds[winner_id], seeds[loser_id]
            diff = winner_seed - loser_seed
            if diff > 0:
                upsets.append((diff, g, winner_seed, loser_seed))

    for i, (diff, g, wseed, lseed) in enumerate(sorted(upsets, key=lambda x: -x[0])[:top_n], start=1):
        win_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        lose_side = "team2" if win_side == "team1" else "team1"
        w_ref = team_ref(g[f"{win_side}_name"], g[f"{win_side}_mgr"], managers)
        l_ref = team_ref(g[f"{lose_side}_name"], g[f"{lose_side}_mgr"], managers)
        ordinal = ordinal_word(i, "biggest")
        text = pick([
            f"#{wseed} seed {w_ref} knocked out #{lseed} seed {l_ref} in {game_desc(g)} \u2014 the {ordinal} "
            f"seed upset in playoff history.",
            f"Seeding didn't matter in {game_desc(g)}: #{wseed} seed {w_ref} eliminated #{lseed} seed "
            f"{l_ref}, a {diff}-seed upset \u2014 {ordinal} in league history.",
        ])
        if i == 1:
            text += " Chalk lost that one."
        fb.add("playoff_upset", text, value=diff, season=g["season"], week=g["week"], round=g["round"],
               managers=[g["team1_mgr"], g["team2_mgr"]])


def league_wide_facts(fb: FactBook, seasons: dict, games: list[dict], name_to_mgr: dict, years: list[int]):
    for year, d in seasons.items():
        reg_games = [g for g in games if g["season"] == year and g["round"] == "regular_season"]
        scores = [g["team1_score"] for g in reg_games] + [g["team2_score"] for g in reg_games]
        if not scores:
            continue  # no completed weeks yet this season (e.g. right after a fresh in-season file is created)
        avg = sum(scores) / len(scores)
        text = pick([
            f"The {year} regular season averaged {avg:.2f} points per team performance.",
            f"{year} regular-season scoring settled in at {avg:.2f} points per team per week, league-wide.",
        ])
        fb.add("season_average_score", text, value=round(avg, 2), season=year)

    appearances = defaultdict(set)
    for year, d in seasons.items():
        for t in d["teams"].values():
            appearances[t["team_name"]].add(year)

    all_years = set(years)
    for name, name_years in appearances.items():
        if name_years == all_years:
            fb.add("team_name_longevity",
                   f'"{name}" is one of only a handful of team names to survive every single season, '
                   f'{min(all_years)}\u2013{max(all_years)}.',
                   value=len(name_years), managers=[name_to_mgr[name]])


def manager_career_facts(fb: FactBook, seasons: dict, team_seasons: list[dict], managers: dict, years: list[int], min_seasons=3):
    def mname(mid):
        return managers[mid]["display_name"]

    by_mgr = defaultdict(list)
    for ts in team_seasons:
        by_mgr[ts["mgr"]].append(ts)

    career = {}
    for mgr, rows in by_mgr.items():
        wins = sum(r["wins"] for r in rows)
        losses = sum(r["losses"] for r in rows)
        ties = sum(r["ties"] for r in rows)
        games_played = wins + losses + ties
        career[mgr] = dict(
            wins=wins, losses=losses, ties=ties, games_played=games_played,
            win_pct=(wins + 0.5 * ties) / games_played if games_played else 0.0,
            points_for=sum(r["points_for"] for r in rows),
            seasons=sorted(r["season"] for r in rows),
            championships=[], runner_ups=[], third_places=[], playoff_seasons=set(),
            playoff_wins=0, playoff_losses=0,
        )

    for year, d in seasons.items():
        teams = d["teams"]
        po = d["playoffs"]
        seed_team_ids = set(normalize_seeds(po["seeds"]).keys())

        # build tid -> mgr for this season
        tid_to_mgr = {}
        for tid_str, t in teams.items():
            tid_to_mgr[int(tid_str)] = [m for m, rows in by_mgr.items() if any(
                r["season"] == year and r["team_id"] == int(tid_str) for r in rows)]
        tid_to_mgr = {tid: (mgrs[0] if mgrs else None) for tid, mgrs in tid_to_mgr.items()}

        for tid in seed_team_ids:
            m = tid_to_mgr.get(tid)
            if m:
                career[m]["playoff_seasons"].add(year)

        champ = po["championship"]
        if champ["winner_id"] is not None:
            wm, lm = tid_to_mgr.get(champ["winner_id"]), tid_to_mgr.get(champ["loser_id"])
            if wm:
                career[wm]["championships"].append(year)
            if lm:
                career[lm]["runner_ups"].append(year)

        third = po["third_place"]
        if third["winner_id"] is not None:
            wm = tid_to_mgr.get(third["winner_id"])
            if wm:
                career[wm]["third_places"].append(year)

        # career playoff win-loss record, across every bracket round
        rounds_data = playoff_rounds(po)
        all_playoff_games = (rounds_data["quarterfinal"] + rounds_data["semifinal"]
                              + rounds_data["fifth_place"]
                              + [rounds_data["championship"], rounds_data["third_place"]])
        for m in all_playoff_games:
            w, l = m.get("winner_id"), m.get("loser_id")
            if w is None:
                continue
            wm, lm = tid_to_mgr.get(w), tid_to_mgr.get(l)
            if wm:
                career[wm]["playoff_wins"] += 1
            if lm:
                career[lm]["playoff_losses"] += 1

    # leaderboards
    ranked_wins = sorted(career.items(), key=lambda x: -x[1]["wins"])
    for i, (mgr, c) in enumerate(ranked_wins[:5], start=1):
        ppg = c["points_for"] / c["games_played"] if c["games_played"] else 0.0
        ordinal = ordinal_word(i, "most")
        tie_note = f"-{c['ties']}" if c["ties"] else ""
        text = pick([
            f"{mname(mgr)} is a career {c['wins']}-{c['losses']}{tie_note} across {len(c['seasons'])} "
            f"seasons ({ppg:.2f} PPG) \u2014 the {ordinal} wins in league history.",
            f"Across {len(c['seasons'])} seasons in the league, {mname(mgr)} has piled up {c['wins']} "
            f"career wins \u2014 {ordinal} all-time.",
        ])
        fb.add("career_wins_leaderboard", text, value=c["wins"], managers=[mgr])

    eligible = [(m, c) for m, c in career.items() if len(c["seasons"]) >= min_seasons]
    ranked_pct = sorted(eligible, key=lambda x: -x[1]["win_pct"])
    for i, (mgr, c) in enumerate(ranked_pct[:5], start=1):
        tie_note = f"-{c['ties']}" if c["ties"] else ""
        ordinal = ordinal_word(i, "best")
        text = (f"{mname(mgr)} carries a career {c['wins']}-{c['losses']}{tie_note} record "
                f"({c['win_pct']*100:.1f}%) across {len(c['seasons'])} seasons \u2014 the {ordinal} all-time "
                f"win rate in league history (min. {min_seasons} seasons played).")
        fb.add("career_win_pct_leaderboard", text, value=c["win_pct"], managers=[mgr])

    champs = [(m, c) for m, c in career.items() if c["championships"]]
    for mgr, c in sorted(champs, key=lambda x: -len(x[1]["championships"])):
        n = len(c["championships"])
        yrs = ", ".join(str(y) for y in sorted(c["championships"]))
        text = f"{mname(mgr)} has won {n} championship{'s' if n != 1 else ''} ({yrs})."
        if n >= 3:
            text += " A bona fide dynasty."
        fb.add("career_championships", text, value=n, managers=[mgr])

    bridesmaids = [(m, c) for m, c in career.items() if c["runner_ups"] and not c["championships"]]
    for mgr, c in sorted(bridesmaids, key=lambda x: -len(x[1]["runner_ups"])):
        n = len(c["runner_ups"])
        yrs = ", ".join(str(y) for y in sorted(c["runner_ups"]))
        text = (f"{mname(mgr)} has reached the championship game {n} time{'s' if n != 1 else ''} "
                f"({yrs}) without ever winning it.")
        if n >= 2:
            text += " Close enough to taste it, not enough to have it."
        fb.add("career_bridesmaid", text, value=n, managers=[mgr])

    last_season = max(seasons.keys())
    for mgr, c in champs:
        last_title = max(c["championships"])
        drought = last_season - last_title
        if drought >= 3:
            text = pick([
                f"{mname(mgr)} hasn't hoisted the trophy since {last_title} \u2014 a {drought}-season drought "
                f"and counting.",
                f"It's been {drought} seasons since {mname(mgr)}'s last championship ({last_title}).",
            ])
            fb.add("championship_drought", text, value=drought, managers=[mgr])

    never_playoffs = [(m, c) for m, c in career.items() if not c["playoff_seasons"] and len(c["seasons"]) >= 2]
    for mgr, c in sorted(never_playoffs, key=lambda x: -len(x[1]["seasons"])):
        n = len(c["seasons"])
        text = pick([
            f"{mname(mgr)} has never made the playoffs, across {n} seasons in the league.",
            f"{n} seasons, zero playoff appearances \u2014 {mname(mgr)}'s franchise is still chasing its "
            f"first taste of the postseason.",
        ])
        fb.add("never_made_playoffs", text, value=n, managers=[mgr])

    always_playoffs = [(m, c) for m, c in career.items()
                        if len(c["seasons"]) >= min_seasons and len(c["playoff_seasons"]) == len(c["seasons"])]
    for mgr, c in sorted(always_playoffs, key=lambda x: -len(x[1]["seasons"])):
        fb.add("perfect_playoff_streak",
               f"{mname(mgr)} has made the playoffs in every one of their {len(c['seasons'])} seasons in "
               f"the league \u2014 a perfect record.",
               value=len(c["seasons"]), managers=[mgr])

    ranked_appearances = sorted(career.items(), key=lambda x: -len(x[1]["playoff_seasons"]))
    for i, (mgr, c) in enumerate(ranked_appearances[:5], start=1):
        if not c["playoff_seasons"]:
            continue
        ordinal = ordinal_word(i, "most")
        fb.add("career_playoff_appearances",
               f"{mname(mgr)} has made the playoffs {len(c['playoff_seasons'])} times \u2014 the {ordinal} "
               f"in league history.",
               value=len(c["playoff_seasons"]), managers=[mgr])

    # longest RUN of consecutive years making the playoffs -- distinct from
    # perfect_playoff_streak above, which requires a perfect career rate.
    # A gap year (not played, or played but missed) breaks the streak.
    streaks = []
    for mgr, c in career.items():
        played, made = set(c["seasons"]), c["playoff_seasons"]
        best_len = cur_len = 0
        best_end = cur_end = None
        for y in range(min(years), max(years) + 1):
            if y in played and y in made:
                cur_len += 1
                cur_end = y
            else:
                cur_len = 0
            if cur_len > best_len:
                best_len, best_end = cur_len, cur_end
        if best_len >= 3:
            streaks.append((mgr, best_len, best_end - best_len + 1, best_end))

    for i, (mgr, length, start, end) in enumerate(sorted(streaks, key=lambda x: -x[1])[:5], start=1):
        ordinal = ordinal_word(i, "longest")
        fb.add("playoff_appearance_streak",
               f"{mname(mgr)} made the playoffs in {length} consecutive seasons ({start}-{end}) \u2014 "
               f"the {ordinal} active or historical run in league history.",
               value=length, managers=[mgr])

    # career playoff win-loss record (distinct from career_playoff_appearances,
    # which only counts how many times someone got in, not how they did once there)
    min_playoff_games = 2
    playoff_record_eligible = [(m, c) for m, c in career.items()
                                if c["playoff_wins"] + c["playoff_losses"] >= min_playoff_games]
    ranked_playoff_pct = sorted(
        playoff_record_eligible,
        key=lambda x: -(x[1]["playoff_wins"] / (x[1]["playoff_wins"] + x[1]["playoff_losses"])))
    for i, (mgr, c) in enumerate(ranked_playoff_pct[:5], start=1):
        total = c["playoff_wins"] + c["playoff_losses"]
        pct = c["playoff_wins"] / total * 100
        ordinal = ordinal_word(i, "best")
        fb.add("career_playoff_record",
               f"{mname(mgr)} has a career playoff record of {c['playoff_wins']}-{c['playoff_losses']} "
               f"({pct:.0f}%) \u2014 the {ordinal} playoff win rate in league history "
               f"(min. {min_playoff_games} playoff games).",
               value=pct, managers=[mgr])

    return career


def head_to_head_facts(fb: FactBook, games: list[dict], managers: dict, min_meetings_for_extras=3, min_meetings_for_shutout=3):
    def mname(mid):
        return managers[mid]["display_name"]

    pairs = defaultdict(list)
    for g in games:
        if g["team1_mgr"] == g["team2_mgr"]:
            continue  # shouldn't happen, but guard anyway
        key = tuple(sorted([g["team1_mgr"], g["team2_mgr"]]))
        pairs[key].append(g)

    pair_summaries = []
    for (m1, m2), gs in pairs.items():
        w1 = sum(1 for g in gs if g["winner_mgr"] == m1)
        w2 = sum(1 for g in gs if g["winner_mgr"] == m2)
        ties = sum(1 for g in gs if g["is_tie"])
        total = len(gs)
        gs_sorted = sorted(gs, key=lambda g: (g["season"], g["week"]))
        first = gs_sorted[0]
        playoff_meetings = [g for g in gs if g["round"] != "regular_season"]

        if w1 > w2:
            leader, trailer, lw, tw = m1, m2, w1, w2
        elif w2 > w1:
            leader, trailer, lw, tw = m2, m1, w2, w1
        else:
            leader = trailer = None

        tie_note = f"-{ties}" if ties else ""
        mw = meetings_word(total)
        if leader:
            text = pick([
                f"{mname(leader)} is a career {lw}-{tw}{tie_note} against {mname(trailer)}, spanning "
                f"{total} {mw}.",
                f"{mname(leader)} leads the all-time series {lw}-{tw}{tie_note} over {mname(trailer)} "
                f"({total} {mw} and counting).",
            ])
            fb.add("head_to_head_record", text, value=lw - tw, managers=[m1, m2])
        else:
            fb.add("head_to_head_record",
                   f"{mname(m1)} and {mname(m2)} are dead even, {w1}-{w2}{tie_note}, across {total} "
                   f"all-time {mw} \u2014 as balanced a rivalry as this league has.",
                   value=0, managers=[m1, m2])

        fb.add("head_to_head_first_meeting",
               f"{mname(m1)} and {mname(m2)} first squared off in {game_desc(first)} "
               f"({first['team1_name']} {first['team1_score']:.2f} - {first['team2_score']:.2f} "
               f"{first['team2_name']}).",
               value=first["season"], season=first["season"], week=first["week"], round=first["round"],
               managers=[m1, m2])

        if total >= min_meetings_for_extras:
            closest = min(gs, key=lambda g: g["margin"])
            highest = max(gs, key=lambda g: g["combined"])
            fb.add("head_to_head_closest",
                   f"The closest game ever between {mname(m1)} and {mname(m2)} was a {closest['margin']:.2f}"
                   f"-point margin in {game_desc(closest)}.",
                   value=closest["margin"], season=closest["season"], week=closest["week"],
                   round=closest["round"], managers=[m1, m2])
            fb.add("head_to_head_highest_combined",
                   f"{mname(m1)} and {mname(m2)} combined for {highest['combined']:.2f} points in "
                   f"{game_desc(highest)} \u2014 their highest-scoring meeting ever.",
                   value=highest["combined"], season=highest["season"], week=highest["week"],
                   round=highest["round"], managers=[m1, m2])

        if not playoff_meetings and total >= min_meetings_for_extras:
            fb.add("head_to_head_never_playoffs",
                   f"Despite playing {total} times, {mname(m1)} and {mname(m2)} have never crossed paths "
                   f"in the playoffs.",
                   value=total, managers=[m1, m2])

        if total >= min_meetings_for_shutout and (w1 == 0 or w2 == 0):
            zero_mgr, other_mgr, other_w = (m1, m2, w2) if w1 == 0 else (m2, m1, w1)
            text = pick([
                f"{mname(zero_mgr)} is winless against {mname(other_mgr)}, 0-{other_w} across their history.",
                f"{mname(other_mgr)} owns {mname(zero_mgr)} outright: {other_w}-0 all-time, no ties.",
            ])
            fb.add("head_to_head_shutout", text, value=other_w, managers=[m1, m2])

        # longest streak either side has held, chronologically
        best_streak, best_mgr, cur_streak, cur_mgr = 0, None, 0, None
        for g in gs_sorted:
            if g["is_tie"]:
                cur_streak, cur_mgr = 0, None
                continue
            if g["winner_mgr"] == cur_mgr:
                cur_streak += 1
            else:
                cur_mgr, cur_streak = g["winner_mgr"], 1
            if cur_streak > best_streak:
                best_streak, best_mgr = cur_streak, cur_mgr
        if best_streak >= 3:
            other = m1 if best_mgr == m2 else m2
            text = pick([
                f"{mname(best_mgr)}'s longest winning streak against {mname(other)} sits at {best_streak} "
                f"games.",
                f"{mname(best_mgr)} has strung together as many as {best_streak} straight wins against "
                f"{mname(other)}.",
            ])
            fb.add("head_to_head_streak", text, value=best_streak, managers=[m1, m2])

        pair_summaries.append((m1, m2, total))

    for i, (m1, m2, total) in enumerate(sorted(pair_summaries, key=lambda x: -x[2])[:5], start=1):
        ordinal = ordinal_word(i, "most")
        mw = meetings_word(total)
        text = pick([
            f"{mname(m1)} vs {mname(m2)} is the {ordinal}-played rivalry in league history \u2014 {total} "
            f"all-time {mw}.",
            f"Few matchups have run it back as often as {mname(m1)} and {mname(m2)}: {total} {mw} and "
            f"counting, {ordinal} in league history.",
        ])
        fb.add("most_played_rivalry", text, value=total, managers=[m1, m2])


def seed_trend_facts(fb: FactBook, seasons: dict, games: list[dict], team_seasons: list[dict], managers: dict,
                      complete_seasons: set, top_n=5):
    """Has this seed ever won it all? Which seed wins the most/fewest playoff
    games? What's the worst regular-season record to ever win a playoff
    game? All derived from playoffs.seeds plus every non-regular-season game."""

    seeds_by_season = {y: normalize_seeds(d["playoffs"]["seeds"]) for y, d in seasons.items()}
    total_seasons_count = len(complete_seasons)  # an in-progress season hasn't earned a place in this denominator yet

    # has each seed (1-6) ever won the championship?
    champ_years_by_seed = defaultdict(list)
    for g in games:
        if g["round"] != "championship" or g["is_tie"]:
            continue
        seeds = seeds_by_season[g["season"]]
        winner_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        seed = seeds.get(g[f"{winner_side}_id"])
        if seed is not None:
            champ_years_by_seed[seed].append(g["season"])

    for seed in range(1, 7):
        years = sorted(champ_years_by_seed.get(seed, []))
        if years:
            yrs_str = ", ".join(str(y) for y in years)
            n = len(years)
            text = pick([
                f"The #{seed} seed has won the championship {n} time{'s' if n != 1 else ''} in league "
                f"history ({yrs_str}).",
                f"#{seed} seeds are {n}-for-{total_seasons_count} in championships won ({yrs_str}).",
            ])
        else:
            text = f"No #{seed} seed has ever won the championship, across {total_seasons_count} seasons of league history."
        fb.add("seed_champion_history", text, value=len(years))

    rates = [(seed, len(champ_years_by_seed.get(seed, [])) / total_seasons_count) for seed in range(1, 7)]
    for i, (seed, rate) in enumerate(sorted(rates, key=lambda x: -x[1])[:3], start=1):
        if rate == 0:
            continue
        ordinal = ordinal_word(i, "highest")
        fb.add("seed_championship_rate",
               f"The #{seed} seed has the {ordinal} championship rate of any seed: {rate*100:.0f}% of "
               f"seasons.",
               value=rate)

    # playoff GAME wins by seed, across every bracket round
    wins_by_seed = defaultdict(int)
    for g in games:
        if g["round"] == "regular_season" or g["is_tie"]:
            continue
        seeds = seeds_by_season[g["season"]]
        winner_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        seed = seeds.get(g[f"{winner_side}_id"])
        if seed is not None:
            wins_by_seed[seed] += 1

    if wins_by_seed:
        ranked = sorted(wins_by_seed.items(), key=lambda x: x[1])
        fewest_seed, fewest_n = ranked[0]
        fb.add("seed_playoff_wins",
               f"The #{fewest_seed} seed has won the fewest playoff games of any seed in league history, "
               f"just {fewest_n}.",
               value=fewest_n)
        most_seed, most_n = ranked[-1]
        fb.add("seed_playoff_wins",
               f"The #{most_seed} seed has won the most playoff games of any seed in league history: "
               f"{most_n}.",
               value=most_n)

    # worst regular-season record to still win a playoff game -- dedupe by
    # (season, team) first, since a team can win multiple playoff games in
    # the same postseason and shouldn't occupy multiple ranks for one record
    wins_lookup = {(ts["season"], ts["team_id"]): ts["wins"] for ts in team_seasons}
    floor_by_team_season = {}
    for g in games:
        if g["round"] == "regular_season" or g["is_tie"]:
            continue
        winner_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        key = (g["season"], g[f"{winner_side}_id"])
        if key not in floor_by_team_season:
            reg_wins = wins_lookup.get(key)
            if reg_wins is not None:
                floor_by_team_season[key] = (reg_wins, g, winner_side)

    floor_candidates = list(floor_by_team_season.values())

    for i, (reg_wins, g, winner_side) in enumerate(sorted(floor_candidates, key=lambda x: x[0])[:top_n], start=1):
        w_ref = team_ref(g[f"{winner_side}_name"], g[f"{winner_side}_mgr"], managers)
        ordinal = ordinal_word(i, "fewest")
        fb.add("worst_record_to_win_playoff_game",
               f"{w_ref} won a playoff game in {g['season']} with just {reg_wins} regular-season wins "
               f"\u2014 the {ordinal} regular-season wins by a team that still won a playoff game.",
               value=reg_wins, season=g["season"], week=g["week"], round=g["round"],
               managers=[g[f"{winner_side}_mgr"]])


def pf_pa_outlier_facts(fb: FactBook, seasons: dict, team_seasons: list[dict], managers: dict, top_n=3):
    """Teams that made or missed the playoffs despite a surprising points-for
    or points-against rank that season -- did they get there on record alone,
    or get left out despite the scoreboard saying otherwise."""
    by_season = defaultdict(list)
    for ts in team_seasons:
        by_season[ts["season"]].append(ts)

    low_pf_made, high_pf_missed = [], []
    good_pa_missed, bad_pa_made = [], []

    for year, rows in by_season.items():
        if not is_season_complete(seasons[year]):
            continue  # playoff qualification isn't decided yet -- can't label anyone "missed"
        made_ids = set(normalize_seeds(seasons[year]["playoffs"]["seeds"]).keys())

        by_pf = sorted(rows, key=lambda t: -t["points_for"])
        for rank, ts in enumerate(by_pf, start=1):
            (low_pf_made if ts["team_id"] in made_ids else high_pf_missed).append((rank, ts))

        by_pa = sorted(rows, key=lambda t: t["points_against"])  # rank 1 = fewest allowed (best defense)
        for rank, ts in enumerate(by_pa, start=1):
            (bad_pa_made if ts["team_id"] in made_ids else good_pa_missed).append((rank, ts))

    for i, (rank, ts) in enumerate(sorted(low_pf_made, key=lambda x: -x[0])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        fb.add("low_pf_rank_made_playoffs",
               f"{r} made the playoffs in {ts['season']} with just the {bare_ordinal(rank)}-most points "
               f"scored in the league that season.",
               value=rank, season=ts["season"], managers=[ts["mgr"]])

    for i, (rank, ts) in enumerate(sorted(high_pf_missed, key=lambda x: x[0])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        fb.add("high_pf_rank_missed_playoffs",
               f"{r} missed the playoffs in {ts['season']} despite having the {bare_ordinal(rank)}-most "
               f"points scored in the league that season.",
               value=rank, season=ts["season"], managers=[ts["mgr"]])

    for i, (rank, ts) in enumerate(sorted(good_pa_missed, key=lambda x: x[0])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        fb.add("low_pa_missed_playoffs",
               f"{r} missed the playoffs in {ts['season']} despite allowing the {bare_ordinal(rank)}-fewest "
               f"points in the league that season.",
               value=rank, season=ts["season"], managers=[ts["mgr"]])

    for i, (rank, ts) in enumerate(sorted(bad_pa_made, key=lambda x: -x[0])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        fb.add("high_pa_made_playoffs",
               f"{r} made the playoffs in {ts['season']} despite allowing the {bare_ordinal(rank)}-most "
               f"points in the league that season.",
               value=rank, season=ts["season"], managers=[ts["mgr"]])


def luck_facts(fb: FactBook, games: list[dict], team_seasons: list[dict], managers: dict, top_n=5):
    """Luckiest win / unluckiest loss (won or lost despite a bad/good week
    relative to the whole league), plus season-level expected wins (X-Win,
    matching the league's own recap terminology) over/underperformers."""

    scores_by_week = defaultdict(dict)
    for g in games:
        if g["round"] != "regular_season":
            continue
        scores_by_week[(g["season"], g["week"])][g["team1_id"]] = g["team1_score"]
        scores_by_week[(g["season"], g["week"])][g["team2_id"]] = g["team2_score"]

    weekly_rank = {}
    for key, team_scores in scores_by_week.items():
        ordered = sorted(team_scores.items(), key=lambda x: -x[1])
        weekly_rank[key] = {tid: i + 1 for i, (tid, _) in enumerate(ordered)}

    lucky_wins, unlucky_losses = [], []
    for g in games:
        if g["round"] != "regular_season" or g["is_tie"]:
            continue
        ranks = weekly_rank[(g["season"], g["week"])]
        winner_side = "team1" if g["team1_score"] > g["team2_score"] else "team2"
        loser_side = "team2" if winner_side == "team1" else "team1"
        w_rank = ranks[g[f"{winner_side}_id"]]
        l_rank = ranks[g[f"{loser_side}_id"]]
        lucky_wins.append((w_rank, g, winner_side, loser_side))
        unlucky_losses.append((l_rank, g, winner_side, loser_side))

    for i, (w_rank, g, winner_side, loser_side) in enumerate(sorted(lucky_wins, key=lambda x: -x[0])[:top_n], start=1):
        w_ref = team_ref(g[f"{winner_side}_name"], g[f"{winner_side}_mgr"], managers)
        l_ref = team_ref(g[f"{loser_side}_name"], g[f"{loser_side}_mgr"], managers)
        ordinal = ordinal_word(i, "luckiest")
        fb.add("luckiest_win",
               f"{w_ref} beat {l_ref} in {game_desc(g)} despite having only the {bare_ordinal(w_rank)}"
               f"-highest score in the league that week \u2014 the {ordinal} win in league history.",
               value=w_rank, season=g["season"], week=g["week"], round=g["round"],
               managers=[g[f"{winner_side}_mgr"], g[f"{loser_side}_mgr"]])

    for i, (l_rank, g, winner_side, loser_side) in enumerate(sorted(unlucky_losses, key=lambda x: x[0])[:top_n], start=1):
        w_ref = team_ref(g[f"{winner_side}_name"], g[f"{winner_side}_mgr"], managers)
        l_ref = team_ref(g[f"{loser_side}_name"], g[f"{loser_side}_mgr"], managers)
        ordinal = ordinal_word(i, "unluckiest")
        fb.add("unluckiest_loss",
               f"{l_ref} lost to {w_ref} in {game_desc(g)} despite having the {bare_ordinal(l_rank)}"
               f"-highest score in the league that week \u2014 the {ordinal} loss in league history.",
               value=l_rank, season=g["season"], week=g["week"], round=g["round"],
               managers=[g[f"{winner_side}_mgr"], g[f"{loser_side}_mgr"]])

    # expected wins (X-Win), all-play method: each week, a team "beats" every
    # other team it outscored that week; summed across the season this gives
    # the record their scoring alone would predict, regardless of schedule.
    exp_wins = defaultdict(float)
    for key, team_scores in scores_by_week.items():
        n = len(team_scores) - 1
        if n <= 0:
            continue
        for tid, score in team_scores.items():
            beat = sum(1 for other, oscore in team_scores.items() if other != tid and score > oscore)
            exp_wins[(key[0], tid)] += beat / n

    luck_rows = [(ts["wins"] - exp_wins.get((ts["season"], ts["team_id"]), 0.0), ts,
                  exp_wins.get((ts["season"], ts["team_id"]), 0.0)) for ts in team_seasons]

    for i, (diff, ts, xw) in enumerate(sorted(luck_rows, key=lambda x: -x[0])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "luckiest")
        fb.add("luckiest_season",
               f"{r} went {ts['wins']}-{ts['losses']}-{ts['ties']} in {ts['season']} despite an expected "
               f"record of just {xw:.1f} wins by scoring alone \u2014 the {ordinal} season (by X-Win) in "
               f"league history.",
               value=diff, season=ts["season"], managers=[ts["mgr"]])

    for i, (diff, ts, xw) in enumerate(sorted(luck_rows, key=lambda x: x[0])[:top_n], start=1):
        r = team_ref(ts["team_name"], ts["mgr"], managers)
        ordinal = ordinal_word(i, "unluckiest")
        fb.add("unluckiest_season",
               f"{r} went just {ts['wins']}-{ts['losses']}-{ts['ties']} in {ts['season']} despite an "
               f"expected record of {xw:.1f} wins by scoring alone \u2014 the {ordinal} season (by X-Win) "
               f"in league history.",
               value=diff, season=ts["season"], managers=[ts["mgr"]])


def cross_season_streak_facts(fb: FactBook, games: list[dict], managers: dict, min_length=4):
    """Win/loss streaks that cross a season boundary -- the last games of one
    year running straight into the first games of the next."""

    def mname(mid):
        return managers[mid]["display_name"]

    by_mgr_games = defaultdict(list)
    for g in games:
        if g["round"] != "regular_season" or g["is_tie"]:
            continue
        for side in ("team1", "team2"):
            mgr = g[f"{side}_mgr"]
            result = "Win" if g["winner_mgr"] == mgr else "Loss"
            by_mgr_games[mgr].append((g["season"], g["week"], result))

    streaks = []
    for mgr, entries in by_mgr_games.items():
        entries.sort(key=lambda x: (x[0], x[1]))
        for target in ("Win", "Loss"):
            best_len = cur_len = 0
            best_start = best_end = cur_start = cur_end = None
            for season, week, result in entries:
                if result == target:
                    if cur_len == 0:
                        cur_start = season
                    cur_len += 1
                    cur_end = season
                else:
                    if cur_len > best_len:
                        best_len, best_start, best_end = cur_len, cur_start, cur_end
                    cur_len = 0
            if cur_len > best_len:
                best_len, best_start, best_end = cur_len, cur_start, cur_end
            if best_len >= min_length and best_start != best_end:
                streaks.append((mgr, best_len, target, best_start, best_end))

    for kind, cat, verb in [("Win", "cross_season_win_streak", "won"), ("Loss", "cross_season_loss_streak", "lost")]:
        subset = sorted([s for s in streaks if s[2] == kind], key=lambda x: -x[1])[:5]
        for i, (mgr, length, _, start, end) in enumerate(subset, start=1):
            ordinal = ordinal_word(i, "longest")
            fb.add(cat,
                   f"{mname(mgr)} {verb} {length} straight games spanning the {start}-{end} seasons \u2014 "
                   f"the {ordinal} streak to cross a season boundary in league history.",
                   value=length, managers=[mgr])


def year_over_year_facts(fb: FactBook, team_seasons: list[dict], managers: dict, complete_seasons: set, top_n=5):
    """Biggest single-season win-total swing for the same manager, one year
    to the next -- the "5, 2, 5, 6, 5 wins, then 10" type turnaround."""

    def mname(mid):
        return managers[mid]["display_name"]

    by_mgr_season = defaultdict(dict)
    for ts in team_seasons:
        by_mgr_season[ts["mgr"]][ts["season"]] = ts["wins"]

    swings = []
    for mgr, season_wins in by_mgr_season.items():
        years = sorted(season_wins.keys())
        for y1, y2 in zip(years, years[1:]):
            if y2 != y1 + 1:
                continue  # only directly consecutive seasons count
            if y1 not in complete_seasons or y2 not in complete_seasons:
                continue  # comparing a partial season's win total to a full one is misleading
            swings.append((season_wins[y2] - season_wins[y1], mgr, y1, y2,
                            season_wins[y1], season_wins[y2]))

    for i, (swing, mgr, y1, y2, w1, w2) in enumerate(sorted(swings, key=lambda x: -x[0])[:top_n], start=1):
        if swing <= 0:
            continue
        ordinal = ordinal_word(i, "biggest")
        fb.add("year_over_year_improvement",
               f"{mname(mgr)} improved from {w1} wins in {y1} to {w2} wins in {y2}, a swing of +{swing} "
               f"\u2014 the {ordinal} single-season turnaround in league history.",
               value=swing, season=y2, managers=[mgr])

    for i, (swing, mgr, y1, y2, w1, w2) in enumerate(sorted(swings, key=lambda x: x[0])[:top_n], start=1):
        if swing >= 0:
            continue
        ordinal = ordinal_word(i, "biggest")
        fb.add("year_over_year_decline",
               f"{mname(mgr)} fell from {w1} wins in {y1} to {w2} wins in {y2}, a swing of {swing} \u2014 "
               f"the {ordinal} single-season collapse in league history.",
               value=swing, season=y2, managers=[mgr])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/history", type=Path)
    parser.add_argument("--mapping", default="manager_mapping.json", type=Path)
    parser.add_argument("--managers", default="managers.json", type=Path)
    parser.add_argument("--out", default="facts.json", type=Path)
    args = parser.parse_args()

    seasons, name_to_mgr, managers, years = load_all(args.data_dir, args.mapping, args.managers)
    games = build_games(seasons, name_to_mgr)
    team_seasons = build_team_seasons(seasons, name_to_mgr)
    complete_seasons = {y for y, d in seasons.items() if is_season_complete(d)}

    fb = FactBook()
    single_game_records(fb, games, managers)
    season_records(fb, team_seasons, managers, complete_seasons)
    playoff_facts(fb, seasons, games, managers, name_to_mgr)
    league_wide_facts(fb, seasons, games, name_to_mgr, years)
    manager_career_facts(fb, seasons, team_seasons, managers, years)
    head_to_head_facts(fb, games, managers)
    seed_trend_facts(fb, seasons, games, team_seasons, managers, complete_seasons)
    pf_pa_outlier_facts(fb, seasons, team_seasons, managers)
    luck_facts(fb, games, team_seasons, managers)
    cross_season_streak_facts(fb, games, managers)
    year_over_year_facts(fb, team_seasons, managers, complete_seasons)

    args.out.write_text(json.dumps(fb.facts, indent=2, ensure_ascii=False))

    by_cat = defaultdict(int)
    for f in fb.facts:
        by_cat[f["category"]] += 1
    print(f"wrote {len(fb.facts)} facts -> {args.out}")
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat:32s} {n}")


if __name__ == "__main__":
    main()
