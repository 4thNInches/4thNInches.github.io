"""
league_config.py

Single source of truth for the handful of values that genuinely differ
from league to league: the Sleeper league id, the manually-curated roster
of manager identities, and the recap-award display names. Every script
that needs one of these imports it from here rather than hardcoding its
own copy -- found FOUR separate hardcoded copies of SLEEPER_LEAGUE_ID
alone (compute_power_stats.py, export_sleeper_season.py, script.js,
recaps.js) before this existed, which is exactly the kind of drift that
made the SLEEPER_LEAGUE_ID / RECAPS_SLEEPER_LEAGUE_ID naming workaround in
recaps.js necessary in the first place.

To point this whole site at a different Sleeper league: edit
data/league_config.json. Nothing in the Python layer (or the JS layer --
see script.js's/recaps.js's own config fetch) should need to change.

Deliberately dependency-free (stdlib only), unlike compute_power_stats.py
(which needs numpy/requests) -- so a script that doesn't otherwise need
those, like export_sleeper_season.py, can import this without picking up
an unrelated dependency it would then need adding to its own workflow's
install step (see the update-feed.yml numpy gap this exact pattern caused
once already).

manager_identities is Sleeper user_id -> manager_id: a small, manually-
curated, RARELY-changing table (only touched when a manager joins or
leaves the league) -- deliberately kept separate from
data/manager_mapping.json, which is a DIFFERENT, auto-regenerated mapping
(current team_name -> manager_id) that export_sleeper_season.py rewrites
every single run as team names change. Merging the two would mix a
hand-maintained identity table with a machine-regenerated one that has a
completely different mutation pattern.

NOTE: script.js also references data/sleeper_manager_mapping.json and
data/managers.json for its own standings/lifetime tables -- two files
this project hasn't seen the contents of. They may or may not overlap
with manager_identities below (sleeper_manager_mapping.json appears to be
keyed by Sleeper display_name rather than user_id, which would make it a
genuinely different lookup, not just a duplicate -- display names can be
changed by the account holder, unlike user_id). Left untouched rather
than guessed at; worth a manual look before assuming it's redundant with
this file.
"""

import json
from pathlib import Path

CONFIG_PATH = Path("data/league_config.json")

# Generic fallback names -- used only if data/league_config.json omits
# award_names entirely, so a league that hasn't customized these yet
# still gets a working default rather than a KeyError.
DEFAULT_AWARD_NAMES = {
    "best_bench": "Best Bench",
    "you_didnt_lose": "You Didn't Lose!",
    "great_defense": "Great Defense!",
    "mega_blowout": "Mega Blowout",
}


def load_league_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"{CONFIG_PATH} not found -- every script in this pipeline needs it. "
            f"See league_config.py's module docstring for what it should contain."
        )
    return json.loads(CONFIG_PATH.read_text())


_config = load_league_config()

SLEEPER_LEAGUE_ID = _config["sleeper_league_id"]
SLEEPER_USER_TO_MANAGER = _config.get("manager_identities", {})
AWARD_NAMES = {**DEFAULT_AWARD_NAMES, **_config.get("award_names", {})}
