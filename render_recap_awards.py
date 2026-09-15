"""
render_recap_awards.py

The phrasing layer for last week's recap awards -- takes
generate_recap_awards.py's structured output and turns it into readable
prose, using the same pick()-style deterministic phrasing-variant pattern
as render_matchup_previews.py (and, before that, generate_facts.py).

Each award gets exactly one line of prose, chosen from a small phrasing
bank, seeded per (season, week, award type) so re-rendering the same
week's data doesn't reshuffle the wording every time re-run.

Usage:
    python render_recap_awards.py data/recap_storylines/2026-wk2.json
    python render_recap_awards.py data/recap_storylines/2026-wk2.json --json
    python render_recap_awards.py   # auto-detects last completed week
"""

import argparse
import json
import random
from pathlib import Path

from generate_recap_awards import AWARD_NAMES


def pick(options: list, seed_key: str):
    return random.Random(seed_key).choice(options)


def _ordinal(n) -> str:
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ============================================================
# Per-award-type renderers -- each returns one phrasing-variant sentence.
# ============================================================

def render_best_bench(data: dict, seed_key: str) -> str:
    team, bench_pts = data["team_name"], data["bench_points"]
    top = data["top_bench_player"]
    variants = [
        f"{team} left {bench_pts} points on the bench this week -- including {top['points']} from "
        f"{top['name']}, who never even got on the field.",
        f"Nobody stranded more points on the bench than {team} ({bench_pts}), headlined by "
        f"{top['name']}'s {top['points']}-point no-show in the starting lineup.",
        f"{team}'s bench outscored their good sense: {bench_pts} points left sitting, "
        f"{top['points']} of it from {top['name']} alone.",
    ]
    return pick(variants, seed_key)


def render_you_didnt_lose(data: dict, seed_key: str) -> str:
    w, l = data["winner"], data["loser"]
    variants = [
        f"{w['team_name']} scored just {w['score']} -- the {_ordinal(w['rank'])}-highest score in the "
        f"league this week -- but {l['team_name']} somehow scored even less ({l['score']}), so the win counts all the same.",
        f"{w['team_name']} put up a bottom-half {w['score']} and still walked away with a win, "
        f"thanks to {l['team_name']}'s {l['score']}.",
        f"Proof records can lie: {w['team_name']} beat {l['team_name']} despite the "
        f"{_ordinal(w['rank'])}-worst score of the week.",
    ]
    return pick(variants, seed_key)


def render_great_defense(data: dict, seed_key: str) -> str:
    w, l = data["winner"], data["loser"]
    variants = [
        f"{w['team_name']} held {l['team_name']} to just {l['score']} points -- the "
        f"{_ordinal(l['rank'])}-worst score in the league this week -- on the way to the win.",
        f"{l['team_name']} never had a chance against {w['team_name']}, managing only {l['score']} points all week.",
        f"{w['team_name']}'s defense (well, their opponent's offense) did the work: {l['team_name']} "
        f"finished with just {l['score']}.",
    ]
    return pick(variants, seed_key)


def render_mega_blowout(data: dict, seed_key: str) -> str:
    w, l = data["winner"], data["loser"]
    variants = [
        f"{w['team_name']} demolished {l['team_name']} {w['score']}-{l['score']}, a {data['margin']}-point "
        f"margin -- the biggest blowout of the week.",
        f"The week's biggest mismatch: {w['team_name']} over {l['team_name']}, {w['score']}-{l['score']} "
        f"({data['margin']} points).",
        f"Not close: {w['team_name']} beat {l['team_name']} by {data['margin']} points, "
        f"{w['score']} to {l['score']}.",
    ]
    return pick(variants, seed_key)


RENDERERS = {
    "best_bench": render_best_bench,
    "you_didnt_lose": render_you_didnt_lose,
    "great_defense": render_great_defense,
    "mega_blowout": render_mega_blowout,
}


def render_award(award: dict, season: int, week: int) -> dict:
    seed_key = f"{season}-{week}-{award['type']}"
    text = RENDERERS[award["type"]](award["data"], seed_key)
    return {
        "type": award["type"],
        "name": award.get("name") or AWARD_NAMES.get(award["type"], award["type"]),
        "text": text,
    }


def default_recap_path() -> Path:
    """Auto-detects last week's recap-storylines file the same way
    generate_recap_awards.py picks its own target week, so the two
    scripts agree on which file to read/write without anything needing
    to be passed between them in an automated context."""
    import compute_power_stats as pws
    league = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}")
    state = pws.sleeper_get("/state/nfl")
    weeks_info = pws.resolve_weeks(state, league)
    if not weeks_info["completed_weeks"]:
        return None
    week = max(weeks_info["completed_weeks"])
    return Path(f"data/recap_storylines/{league['season']}-wk{week}.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("recap_file", type=Path, nargs="?", default=None,
                         help="Defaults to last week's file, auto-detected the same way generate_recap_awards.py picks its target week")
    parser.add_argument("--out", type=Path, default=None,
                         help="Defaults to data/recaps/<same filename> -- mirrors the storylines/ -> previews/ convention")
    parser.add_argument("--json", action="store_true", help="Also print machine-readable output")
    args = parser.parse_args()

    if args.recap_file is None:
        print("No file given -- auto-detecting last week's recap-storylines file...")
        args.recap_file = default_recap_path()
        if args.recap_file is None:
            print("  no completed weeks yet this season -- nothing to render. Exiting cleanly.")
            return

    if not args.recap_file.exists():
        raise FileNotFoundError(
            f"{args.recap_file} doesn't exist -- run generate_recap_awards.py first "
            f"(or pass an explicit path to an existing file)."
        )

    out_path = args.out or Path(str(args.recap_file).replace("data/recap_storylines", "data/recaps", 1))

    doc = json.loads(args.recap_file.read_text())
    rendered = [render_award(a, doc["season"], doc["week"]) for a in doc["awards"]]

    print(f"\nWeek {doc['week']} recap awards:")
    for r in rendered:
        print(f"  {r['name']}: {r['text']}")

    if args.json:
        print("\n" + json.dumps(rendered, indent=2))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "season": doc["season"], "week": doc["week"], "awards": rendered,
    }, indent=2, ensure_ascii=False))
    print(f"\n  wrote {out_path}")


if __name__ == "__main__":
    main()
