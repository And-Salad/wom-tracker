"""Download the icons used to label chart axes.

Both sources are the ones RuneLite's hiscore lookup panel draws from:

  Skills  RuneLite's own skill_icons resources - the 25x25 interface icons,
          exactly what the hiscore panel shows next to each level.
  Bosses  Wise Old Man's metric icons. RuneLite renders its boss column from
          game-cache sprites rather than files in its repository, so there is
          nothing to download there; Wise Old Man serves the same hiscore
          sprites as PNGs, already named by metric.

Everything lands in assets/<kind>/<metric>.png. The app falls back to text
labels when the files are missing, so this only needs running to add or
refresh them.

    py fetch_icons.py                all of it
    py fetch_icons.py --skills       just the skill icons
    py fetch_icons.py --bosses       just the boss and activity icons
"""

import argparse
import os
import sys
import time

import requests

from wom.icons import BOSS_ICON_DIR, RUNELITE_SKILL_FILES, SKILL_ICON_DIR

USER_AGENT = "WOM-Tracker/1.0 (personal icon fetch)"

RUNELITE_ICON_URL = (
    "https://raw.githubusercontent.com/runelite/runelite/master/runelite-client/"
    "src/main/resources/skill_icons/{}.png"
)
WOM_ICON_URL = "https://wiseoldman.net/img/metrics/{}.png"


def _download(session, url, path, label):
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if not resp.ok or "image" not in resp.headers.get("content-type", ""):
        print("{:<32} no icon (HTTP {})".format(label, resp.status_code))
        return False
    with open(path, "wb") as fh:
        fh.write(resp.content)
    print("{:<32} {:>6} bytes".format(label, len(resp.content)))
    return True


def fetch_skill_icons():
    """Pull the skill icons out of RuneLite's resources."""
    os.makedirs(SKILL_ICON_DIR, exist_ok=True)
    session = requests.Session()
    saved = 0
    for metric, filename in sorted(RUNELITE_SKILL_FILES.items()):
        if _download(session, RUNELITE_ICON_URL.format(filename),
                     os.path.join(SKILL_ICON_DIR, metric + ".png"), metric):
            saved += 1
        time.sleep(0.1)  # be polite
    print("{} skill icons saved to {}".format(saved, SKILL_ICON_DIR))
    return saved


def fetch_boss_icons(metrics):
    """Pull one icon per boss/activity metric from Wise Old Man."""
    os.makedirs(BOSS_ICON_DIR, exist_ok=True)
    session = requests.Session()
    saved = 0
    for metric in sorted(metrics):
        if _download(session, WOM_ICON_URL.format(metric),
                     os.path.join(BOSS_ICON_DIR, metric + ".png"), metric):
            saved += 1
        time.sleep(0.1)
    print("{} boss icons saved to {}".format(saved, BOSS_ICON_DIR))
    return saved


def known_boss_metrics():
    """Every boss and activity metric, from the database if there is one."""
    from wom.config import DB_PATH
    if not os.path.exists(DB_PATH):
        print("no database yet - run an update first so the metric list is known")
        return []
    from wom.db import Database
    rows = Database(DB_PATH).query(
        "SELECT DISTINCT metric FROM metrics WHERE kind IN ('boss', 'activity')")
    return [row["metric"] for row in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skills", action="store_true", help="only the skill icons")
    parser.add_argument("--bosses", action="store_true", help="only the boss icons")
    args = parser.parse_args(argv)

    both = not (args.skills or args.bosses)
    total = 0
    if both or args.skills:
        total += fetch_skill_icons()
    if both or args.bosses:
        total += fetch_boss_icons(known_boss_metrics())
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
