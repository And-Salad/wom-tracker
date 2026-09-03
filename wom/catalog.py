"""What the Overview charts show, independent of how they are drawn.

chartkit.js draws them; this is the half that has to agree with the server, so
the metric lists and dropdown choices live here rather than in the drawing.
"""

from .icons import SKILL_ORDER
from .util import pretty_metric

# How many bosses the boss chart shows, ranked by kills gained.
TOP_BOSSES = 20

# The level chart's dropdown: total level first, then the skills A-Z.
TOTAL_LEVEL = "Total level"
LEVEL_CHOICES = [TOTAL_LEVEL] + sorted(pretty_metric(s) for s in SKILL_ORDER)
CHOICE_METRICS = dict({TOTAL_LEVEL: "overall"},
                      **{pretty_metric(s): s for s in SKILL_ORDER})

# The collection log chart's dropdown. Plotted one at a time rather than
# stacked, so "all" can sit alongside the tiers without double counting.
COLLECTION_LOG = "Collection log"
LOG_CHOICES = [COLLECTION_LOG, "Clue scrolls (all)", "Beginner clues",
               "Easy clues", "Medium clues", "Hard clues", "Elite clues",
               "Master clues"]
LOG_METRICS = {
    COLLECTION_LOG: "collections_logged",
    "Clue scrolls (all)": "clue_scrolls_all",
    "Beginner clues": "clue_scrolls_beginner",
    "Easy clues": "clue_scrolls_easy",
    "Medium clues": "clue_scrolls_medium",
    "Hard clues": "clue_scrolls_hard",
    "Elite clues": "clue_scrolls_elite",
    "Master clues": "clue_scrolls_master",
}


# The two ways a trend card can be read. "Total" plots the stored value, which
# is what the card has always shown. "Gained" plots the change since the window
# opened, and is the only way accounts a thousand levels apart share an axis
# usefully: on the totals, a month of everyone's progress is a few pixels of
# wiggle against the gap between the highest account and the lowest.
#
# Both readings come out of the same payload - the change is the series minus
# the reading the window starts from - so switching redraws without refetching.
TREND_MODES = ["Total", "Gained"]


class ChartSpec:
    """One chart on the Overview page: what it shows, and what builds it."""

    def __init__(self, key, title, kind, description="", options=None,
                 modes=None):
        self.key = key
        self.title = title
        self.kind = kind                  # stacked | trend
        self.description = description
        self.options = list(options) if options else None
        # How the card may be read, if more than one way. The first is what it
        # opens on, so it is always the reading the card had before it had any.
        self.modes = list(modes) if modes else None
        self.build = None                 # set by @chart, below

    def as_dict(self):
        return {"key": self.key, "title": self.title,
                "description": self.description, "kind": self.kind,
                "options": self.options, "modes": self.modes}


def chart(key):
    """Attach a builder to its spec.

    The two used to live in separate files with nothing checking they agreed,
    so a chart could be described and never built, or built under a key
    nothing described. Now the decorator raises on the spot.
    """
    def decorate(func):
        spec = BY_KEY.get(key)
        if spec is None:
            raise KeyError(
                "no chart named {!r} in SUMMARY_CHARTS - describe it first".format(key))
        spec.build = func
        return func
    return decorate


def specs():
    """Every chart that is both described and built, in display order."""
    return [spec for spec in SUMMARY_CHARTS if spec.build is not None]


# The clue tiers, without the "all" roll-up that would double-count them.
CLUE_TIERS = ("clue_scrolls_beginner", "clue_scrolls_easy",
              "clue_scrolls_medium", "clue_scrolls_hard",
              "clue_scrolls_elite", "clue_scrolls_master")

SUMMARY_CHARTS = (
    # Everything else on this tab is per-account. This is the one card that
    # answers "what did we do" rather than "who did what", which is the
    # question the group actually asks each other, so it goes first and the
    # standings below break it down.
    ChartSpec("group_totals", "The group this period", "totals",
              description="Summed across every included account. "
                          "Hover a tile for who contributed what."),
    ChartSpec("standings", "This period", "standings",
              description="Who did what, before the detail below."),
    ChartSpec("skill_gains", "Experience gained by skill", "stacked",
              description="Each column is a skill; each slice is one of the "
                          "included players."),
    # Directly under the bar chart, and deliberately: that one says what the
    # group trained this period, this one says when and how much. Read as a
    # pair, a climb in the line has its explanation in the columns above it.
    ChartSpec("xp_trend", "Experience gained over time", "trend",
              description="Every skill, uncapped, counted from the start of "
                          "the period."),
    ChartSpec("boss_gains", "Top 20 boss kills", "stacked",
              description="The twenty bosses the included players killed most "
                          "this period."),
    ChartSpec("level_trend", "Levels over time", "trend",
              description="One line per included player, over the chosen period.",
              options=LEVEL_CHOICES, modes=TREND_MODES),
    ChartSpec("log_and_clues", "Collection log and clues over time", "trend",
              description="One line per included player, over the chosen period.",
              options=LOG_CHOICES, modes=TREND_MODES),
)

BY_KEY = {spec.key: spec for spec in SUMMARY_CHARTS}
