"""Skill and boss metadata, and the icons used to label chart axes.

The icons mirror RuneLite's hiscore lookup panel: its own skill_icons for the
skills, and the same hiscore boss sprites for the bosses. They live in
assets/<kind>/<metric>.png, named after the Wise Old Man metric, and are served
straight from there by /icon/<kind>/<metric>.png. fetch_icons.py downloads
them; the charts fall back to text labels when one is absent.
"""

import logging
import os
import re

log = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(APP_DIR, "assets")
SKILL_ICON_DIR = os.path.join(ASSET_DIR, "skills")
BOSS_ICON_DIR = os.path.join(ASSET_DIR, "bosses")

# Activities (clues, collection log, minigames) are hiscore rows just like
# bosses and their icons come from the same place, so they share a folder.
ICON_DIRS = {"skill": SKILL_ICON_DIR, "boss": BOSS_ICON_DIR,
             "activity": BOSS_ICON_DIR}

# In-game skills-tab reading order, so the axis looks like the interface.
SKILL_ORDER = (
    "attack", "hitpoints", "mining",
    "strength", "agility", "smithing",
    "defence", "herblore", "fishing",
    "ranged", "thieving", "cooking",
    "prayer", "crafting", "firemaking",
    "magic", "fletching", "woodcutting",
    "runecrafting", "slayer", "farming",
    "construction", "hunter", "sailing",
)

# Not a column on any chart, but achievements like "Base 60 Stats" are filed
# under it, so the Milestones feed needs the icon.
EXTRA_SKILL_ICONS = ("overall",)

# Wise Old Man metric -> the file name in RuneLite's skill_icons directory.
# Only runecrafting differs; RuneLite follows the game and says runecraft.
RUNELITE_SKILL_FILES = dict(
    {skill: skill for skill in SKILL_ORDER + EXTRA_SKILL_ICONS},
    runecrafting="runecraft",
)

# Wise Old Man metric names are lowercase words joined by underscores. Anything
# else is not a metric, and must never reach the filesystem: these names arrive
# from a URL on the web dashboard, which is meant to be shared publicly.
SAFE_METRIC = re.compile(r"\A[a-z0-9_]{1,64}\Z")


def is_safe_metric(metric):
    return bool(SAFE_METRIC.match(str(metric)))


def icon_path(metric, kind="skill"):
    """Where this metric's sprite lives, or None if the name is not one.

    Rejecting the name outright is the guard that matters - on Windows a
    backslash in a URL segment survives routing, so a name built from `..` and
    backslashes would otherwise walk straight out of the asset directory.
    """
    if not is_safe_metric(metric):
        return None
    folder = ICON_DIRS.get(kind, SKILL_ICON_DIR)
    path = os.path.join(folder, "{}.png".format(metric))
    # Belt and braces: whatever the name did, the result has to stay inside.
    if os.path.commonpath([os.path.realpath(folder),
                           os.path.realpath(path)]) != os.path.realpath(folder):
        return None
    return path


def icon_kind_for(metric):
    """Which asset folder holds this metric's icon, or None if neither does."""
    for kind in ("skill", "boss"):
        path = icon_path(metric, kind)
        if path and os.path.exists(path):
            return kind
    return None
