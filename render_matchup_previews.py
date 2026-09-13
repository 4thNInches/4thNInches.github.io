"""
render_matchup_previews.py

The phrasing/template layer explicitly deferred in Section 11/12 of
recaps-roadmap.md -- takes generate_matchup_storylines.py's structured
output and turns it into actual readable prose, using a pick()-style
phrasing bank (same pattern generate_facts.py already uses for the
ticker) rather than an LLM, consistent with the project's core
zero-cost/deterministic design principle.

Two things this deliberately does that are worth knowing:

1. SELECTION: a matchup's storylines list can hold everything that
   qualifies, but a preview shouldn't necessarily use all of it every
   time (see Section 11's "this schema says what's true, not what gets
   used"). v1 rule, deliberately simple: always use head_to_head and
   rivalry_lore when present (bread-and-butter baseline; rare and
   special, respectively), then randomly include up to 2 more from
   whatever else qualifies, seeded per-matchup so re-rendering the same
   week doesn't reshuffle the wording every time.

2. RIVALRY_LORE BLURBS: rivalry_lore.json's "story" field is reference
   prose, not a publish-ready sentence. This pulls just the first
   sentence out of it as a stopgap -- a real fix is adding a proper short
   "blurb" field to that file later (flagged, not solved here).

Usage:
    python render_matchup_previews.py data/storylines/2026-wk1.json
    python render_matchup_previews.py data/storylines/2026-wk1.json --json   # machine-readable output too
"""

import argparse
import json
import random
from pathlib import Path

TEAM_FLAVOR_PATH = Path("data/team_flavor.json")
RIVALRY_LORE_PATH = Path("data/rivalry_lore.json")

MAX_EXTRA_STORYLINES = 2  # beyond the always-included head_to_head/rivalry_lore


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def pick(options: list, seed_key: str):
    return random.Random(seed_key).choice(options)


# ============================================================
# Per-storyline-type renderers -- each returns one sentence (a phrasing
# variant chosen deterministically per matchup) or None if it can't
# render (shouldn't normally happen if the data's well-formed, but never
# crash a whole preview over one bad storyline).
# ============================================================

def render_head_to_head(data: dict, manager_a: str, team_a: str, manager_b: str, team_b: str, seed_key: str) -> str:
    wins_a, wins_b = data["career_record"][manager_a], data["career_record"][manager_b]
    avg_a, avg_b = data["avg_score"][manager_a], data["avg_score"][manager_b]
    n = wins_a + wins_b

    variants = [
        f"{team_a} and {team_b} have met {n} time{'s' if n != 1 else ''} in the regular season, "
        f"with {team_a} averaging {avg_a} points to {team_b}'s {avg_b}.",
    ]
    if wins_a != wins_b:
        leader, ld_w, trailer, tr_w = (team_a, wins_a, team_b, wins_b) if wins_a > wins_b else (team_b, wins_b, team_a, wins_a)
        variants.append(f"{leader} holds a {ld_w}-{tr_w} career edge over {trailer} in the regular season.")

    streak = data.get("current_streak")
    if streak:
        streak_team = team_a if streak["manager_id"] == manager_a else team_b
        variants.append(f"{streak_team} has won {streak['count']} straight in this series.")

    return pick(variants, seed_key)


def render_postseason_history(data: dict, manager_a: str, team_a: str, manager_b: str, team_b: str, seed_key: str) -> str:
    meetings = data["meetings"]
    n = len(meetings)
    most_recent = meetings[-1]
    winner_team = team_a if most_recent["winner"] == manager_a else team_b

    variants = [
        f"{team_a} and {team_b} have crossed paths in the playoffs {n} time{'s' if n != 1 else ''} before.",
        f"The last playoff meeting between these two ({most_recent['season']} {most_recent['round']}) went to {winner_team}.",
    ]
    return pick(variants, seed_key)


def render_notable_matchup(data: dict, manager_a: str, team_a: str, manager_b: str, team_b: str, seed_key: str) -> str:
    total = round(data["score"][manager_a] + data["score"][manager_b], 2)
    variants = [
        f"Their highest-scoring meeting ever came in {data['season']}"
        f"{' Week ' + str(data['week']) if data.get('week') else ''}, when they combined for {total} points.",
    ]
    return pick(variants, seed_key)


def render_team_flavor(data: dict, team_flavor_data: dict, team_name: str, seed_key: str) -> str | None:
    entry = team_flavor_data.get(data["manager_id"])
    nickname = entry.get("nickname") if entry else None
    if not nickname:
        return None
    variants = [
        f"{team_name} is better known to the league as {nickname}.",
        f"Around the league, {team_name} goes by {nickname}.",
    ]
    return pick(variants, seed_key)


def render_rivalry_lore(data: dict, rivalry_lore_data: dict, seed_key: str) -> str | None:
    entry = rivalry_lore_data.get(data["lore_key"])
    if not entry:
        return None
    # Stopgap: first sentence of the reference "story" field. A real fix is
    # a proper short "blurb" field on the entry itself -- see module docstring.
    story = entry.get("story") or ""
    first_sentence = story.split(". ")[0].strip()
    if first_sentence and not first_sentence.endswith("."):
        first_sentence += "."
    return first_sentence or None


def render_roster_strength(data: dict, manager_a: str, team_a: str, manager_b: str, team_b: str, seed_key: str) -> str | None:
    edges = data.get("positional_edge") or []
    if not edges:
        return None
    edge = pick(edges, seed_key)  # feature one position edge, not all of them every time
    leader_team = team_a if edge["leader"] == "a" else team_b
    variants = [
        f"{leader_team} projects to have the edge at {edge['position']} this week.",
    ]
    return pick(variants, seed_key + "_phrasing")


RENDERERS = {
    "head_to_head": render_head_to_head,
    "postseason_history": render_postseason_history,
    "notable_matchup": render_notable_matchup,
}


def render_matchup(matchup: dict, team_flavor_data: dict, rivalry_lore_data: dict) -> str:
    manager_a, team_a = matchup["team_a"]["manager_id"], matchup["team_a"]["team_name"]
    manager_b, team_b = matchup["team_b"]["manager_id"], matchup["team_b"]["team_name"]
    base_seed = f"{manager_a}-{manager_b}"

    # Grouped into LISTS, not a plain {type: data} dict -- team_flavor can
    # legitimately appear twice (once per team) in one matchup's storylines,
    # and a naive dict comprehension keyed by type would silently drop
    # whichever entry came first. Caught by testing a two-nickname matchup
    # specifically, not by inspection.
    by_type: dict = {}
    for s in matchup["storylines"]:
        by_type.setdefault(s["type"], []).append(s["data"])

    sentences = [f"{team_a} takes on {team_b} this week."]

    if "head_to_head" in by_type:
        sentences.append(render_head_to_head(by_type["head_to_head"][0], manager_a, team_a, manager_b, team_b, base_seed + "_h2h"))
    if "rivalry_lore" in by_type:
        lore_sentence = render_rivalry_lore(by_type["rivalry_lore"][0], rivalry_lore_data, base_seed + "_lore")
        if lore_sentence:
            sentences.append(lore_sentence)

    # Randomly-selected extras, deterministic per matchup. team_flavor is
    # one candidate "slot" even when both teams have an entry -- if picked,
    # render ONE of them (chosen the same deterministic way), rather than
    # crowding two nickname call-outs into a single preview every time.
    extra_candidates = [t for t in ("postseason_history", "notable_matchup", "team_flavor", "roster_strength") if t in by_type]
    rng = random.Random(base_seed + "_selection")
    rng.shuffle(extra_candidates)
    used = 0
    for storyline_type in extra_candidates:
        if used >= MAX_EXTRA_STORYLINES:
            break
        seed_key = base_seed + "_" + storyline_type

        if storyline_type == "team_flavor":
            data = pick(by_type["team_flavor"], seed_key + "_which_team")
            meta = matchup["team_a"] if data["manager_id"] == manager_a else matchup["team_b"]
            sentence = render_team_flavor(data, team_flavor_data, meta["team_name"], seed_key)
        elif storyline_type == "roster_strength":
            sentence = render_roster_strength(by_type["roster_strength"][0], manager_a, team_a, manager_b, team_b, seed_key)
        else:
            sentence = RENDERERS[storyline_type](by_type[storyline_type][0], manager_a, team_a, manager_b, team_b, seed_key)

        if sentence:
            sentences.append(sentence)
            used += 1

    return " ".join(sentences)


def default_storylines_path() -> tuple[Path, int, int]:
    """Auto-detects this week's storylines file the same way
    generate_matchup_storylines.py picks its own target week, so the two
    scripts agree on which file to read/write without anything needing to
    be passed between them in an automated context."""
    import compute_power_stats as pws
    league = pws.sleeper_get(f"/league/{pws.SLEEPER_LEAGUE_ID}")
    state = pws.sleeper_get("/state/nfl")
    weeks_info = pws.resolve_weeks(state, league)
    week = weeks_info["forecast_weeks"][0] if weeks_info["forecast_weeks"] else 1
    return Path(f"data/storylines/{league['season']}-wk{week}.json"), league["season"], week


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("storylines_file", type=Path, nargs="?", default=None,
                         help="Defaults to this week's file, auto-detected the same way generate_matchup_storylines.py picks its target week")
    parser.add_argument("--out", type=Path, default=None,
                         help="Defaults to data/previews/<same filename> -- mirrors the storylines/ -> previews/ convention")
    parser.add_argument("--json", action="store_true", help="Also print machine-readable output")
    args = parser.parse_args()

    if args.storylines_file is None:
        print("No file given -- auto-detecting this week's storylines file...")
        args.storylines_file, _, _ = default_storylines_path()

    if not args.storylines_file.exists():
        raise FileNotFoundError(
            f"{args.storylines_file} doesn't exist -- run generate_matchup_storylines.py first "
            f"(or pass an explicit path to an existing file)."
        )

    out_path = args.out or Path(str(args.storylines_file).replace("data/storylines", "data/previews", 1))

    storylines_doc = json.loads(args.storylines_file.read_text())
    team_flavor_data = load_json(TEAM_FLAVOR_PATH)
    rivalry_lore_data = load_json(RIVALRY_LORE_PATH)

    rendered = []
    for matchup in storylines_doc["matchups"]:
        text = render_matchup(matchup, team_flavor_data, rivalry_lore_data)
        rendered.append({"team_a": matchup["team_a"]["team_name"], "team_b": matchup["team_b"]["team_name"], "preview": text})
        print(f"\n{matchup['team_a']['team_name']} vs {matchup['team_b']['team_name']}:")
        print(f"  {text}")

    if args.json:
        print("\n" + json.dumps(rendered, indent=2))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "season": storylines_doc["season"], "week": storylines_doc["week"], "matchups": rendered,
    }, indent=2, ensure_ascii=False))
    print(f"\n  wrote {out_path}")


if __name__ == "__main__":
    main()
