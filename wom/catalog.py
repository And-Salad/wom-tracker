"""What the Summary charts show, independent of how they are drawn.

charts.js draws them; this is the half that has to agree with the server, so
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


class ChartSpec:
    """One chart on the Summary page: what it shows, and what builds it."""

    def __init__(self, key, title, kind, description="", options=None):
        self.key = key
        self.title = title
        self.kind = kind                  # stacked | trend
        self.description = description
        self.options = list(options) if options else None
        self.build = None                 # set by @chart, below

    def as_dict(self):
        return {"key": self.key, "title": self.title,
                "description": self.description, "kind": self.kind,
                "options": self.options}


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


SUMMARY_CHARTS = (
    ChartSpec("standings", "This period", "standings",
              description="Who did what, before the detail below."),
    ChartSpec("skill_gains", "Experience gained by skill", "stacked",
              description="Each column is a skill; each slice is one of the "
                          "included players."),
    ChartSpec("boss_gains", "Top 20 boss kills", "stacked",
              description="The twenty bosses the included players killed most "
                          "this period."),
    ChartSpec("level_trend", "Levels over time", "trend",
              description="One line per included player, over the chosen period.",
              options=LEVEL_CHOICES),
    ChartSpec("log_and_clues", "Collection log and clues over time", "trend",
              description="One line per included player, over the chosen period.",
              options=LOG_CHOICES),
)

BY_KEY = {spec.key: spec for spec in SUMMARY_CHARTS}
