"""
Download and cache Sleeper league team logos for use in local Python plots
(matplotlib, etc).

Usage:
    python fetch_sleeper_logos.py <league_id>

Produces a local `logos/` folder with one PNG per manager, named after their
Sleeper display name, plus a `logos/manifest.json` mapping manager -> file
path -> team name, so your plotting code doesn't need to hit the network
every time it runs.

Sleeper avatars are served from a public CDN, no auth required:
    https://sleepercdn.com/avatars/<avatar_id>          (full size, ~512px)
    https://sleepercdn.com/avatars/thumbs/<avatar_id>   (thumbnail, ~100px)

Each user in a league has:
  - user["avatar"]                -> their personal Sleeper-wide avatar id
  - user["metadata"]["avatar"]    -> a custom avatar set just for THIS
                                      league (their team logo), when present

This script prefers the per-league team avatar when it exists, and falls
back to the personal avatar otherwise.
"""

import json
import sys
from pathlib import Path

import requests

SLEEPER_BASE = "https://api.sleeper.app/v1"
CDN_BASE = "https://sleepercdn.com/avatars"


def fetch_league_users(league_id: str) -> list[dict]:
    resp = requests.get(f"{SLEEPER_BASE}/league/{league_id}/users", timeout=15)
    resp.raise_for_status()
    return resp.json()


def resolve_avatar_id(user: dict) -> str | None:
    metadata = user.get("metadata") or {}
    return metadata.get("avatar") or user.get("avatar")


def download_logo(avatar_id: str, dest: Path, full_size: bool = True) -> bool:
    subpath = "" if full_size else "thumbs/"
    url = f"{CDN_BASE}/{subpath}{avatar_id}"
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200:
        return False
    dest.write_bytes(resp.content)
    return True


def main(league_id: str, out_dir: str = "logos", full_size: bool = True) -> None:
    out_path = Path(out_dir)
    out_path.mkdir(exist_ok=True)

    users = fetch_league_users(league_id)
    manifest = {}

    for user in users:
        manager = user.get("display_name", user["user_id"])
        team_name = (user.get("metadata") or {}).get("team_name", manager)
        avatar_id = resolve_avatar_id(user)

        if not avatar_id:
            print(f"  no avatar for {manager}, skipping")
            continue

        filename = f"{manager}.png".replace("/", "_")
        dest = out_path / filename

        ok = download_logo(avatar_id, dest, full_size=full_size)
        if ok:
            print(f"  saved {manager} -> {dest}")
            manifest[manager] = {"team_name": team_name, "file": str(dest)}
        else:
            print(f"  failed to download logo for {manager}")

    manifest_path = out_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {len(manifest)} logos and manifest to {manifest_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fetch_sleeper_logos.py <league_id>")
        sys.exit(1)
    main(sys.argv[1])


# --- Using the cached logos in a matplotlib plot -----------------------
#
# import json
# import matplotlib.pyplot as plt
# from matplotlib.offsetbox import OffsetImage, AnnotationBbox
#
# manifest = json.loads(open("logos/manifest.json").read())
#
# fig, ax = plt.subplots()
# for manager, info in manifest.items():
#     x, y = ...  # wherever this manager's point goes
#     img = plt.imread(info["file"])
#     ab = AnnotationBbox(OffsetImage(img, zoom=0.15), (x, y), frameon=False)
#     ax.add_artist(ab)
