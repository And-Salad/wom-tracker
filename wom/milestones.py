"""Milestones - 99s, kill counts, base stats - noticed in our own readings.

The definitions are Wise Old Man's achievement templates (server/src/api/
modules/achievements/achievement.templates.ts, MIT licensed, (c) Wise Old
Man), carried over name for name. That is the point of copying rather than
inventing: every milestone already stored came from Wise Old Man and is keyed
on its name, so a crossing noticed here for one it already recorded is the
same row, and `INSERT OR IGNORE` keeps the original date.

A milestone is recorded when one reading falls short of it and the next
meets it - never because a player already meets it. Their back catalogue came
from Wise Old Man when they were added; deciding now that an old 99 happened
today would put it at the top of the feed as news.
"""

from .efficiency import REAL_SKILLS, SKILL_EXP_AT_99
from .hiscores import ACTIVITIES
from .util import parse_api_time

MAX_VIRTUAL_LEVEL = 126


def _xp_table():
    table, xp = [], 0
    for level in range(1, MAX_VIRTUAL_LEVEL + 1):
        table.append(xp // 4)
        xp += int(level + 300 * 2 ** (level / 7))
    return table


XP_FOR_LEVEL = _xp_table()


def exp_for_level(level):
    if level < 1 or level > MAX_VIRTUAL_LEVEL:
        return 0
    return XP_FOR_LEVEL[level - 1]


def level_for_exp(exp):
    if not exp or exp < 0:
        return 1
    level = 1
    for index, needed in enumerate(XP_FOR_LEVEL[:99]):
        if exp >= needed:
            level = index + 1
    return level


SKILL_THRESHOLDS = (13_034_431, 50_000_000, 100_000_000, 200_000_000)
BIG = (500, 1000, 5000, 10_000)
RAID = (100, 500, 1000, 5000)
MINOR = (50, 100, 500, 1000)
RARE = (10, 50, 100, 200)
GAUNTLET = (100, 200, 1000, 2000)

# (name template, metric, thresholds). Measure follows from the metric's
# kind, as Wise Old Man's MetricProps has it, except where a template says.
TEMPLATES = [
    ("{threshold} Overall Exp.", "overall",
     (100_000_000, 200_000_000, 500_000_000, 1_000_000_000,
      2_000_000_000, 4_600_000_000)),
] + [
    ("{threshold} " + skill.capitalize(), skill, SKILL_THRESHOLDS)
    for skill in REAL_SKILLS
] + [
    ("{threshold} Abyssal Sire kills", "abyssal_sire", BIG),
    ("{threshold} Alchemical Hydra kills", "alchemical_hydra", BIG),
    ("{threshold} Amoxliatl kills", "amoxliatl", BIG),
    ("{threshold} Araxxor kills", "araxxor", BIG),
    ("{threshold} Artio kills", "artio", BIG),
    ("{threshold} Barrows Chests", "barrows_chests", BIG),
    ("{threshold} Brutus kills", "brutus", (100, 500, 1000, 5000)),
    ("{threshold} Bryophyta kills", "bryophyta", MINOR),
    ("{threshold} Callisto kills", "callisto", BIG),
    ("{threshold} Calvar'ion kills", "calvarion", BIG),
    ("{threshold} Cerberus kills", "cerberus", BIG),
    ("{threshold} Chambers Of Xeric kills", "chambers_of_xeric", RAID),
    ("{threshold} Chambers Of Xeric (CM) kills",
     "chambers_of_xeric_challenge_mode", RAID),
    ("{threshold} Chaos Elemental kills", "chaos_elemental", BIG),
    ("{threshold} Chaos Fanatic kills", "chaos_fanatic", BIG),
    ("{threshold} Commander Zilyana kills", "commander_zilyana", BIG),
    ("{threshold} Corporeal Beast kills", "corporeal_beast", BIG),
    ("{threshold} Crazy Archaeologist kills", "crazy_archaeologist", BIG),
    ("{threshold} Dagannoth Prime kills", "dagannoth_prime", BIG),
    ("{threshold} Dagannoth Rex kills", "dagannoth_rex", BIG),
    ("{threshold} Dagannoth Supreme kills", "dagannoth_supreme", BIG),
    ("{threshold} Deranged Archaeologist kills", "deranged_archaeologist", BIG),
    ("{threshold} Doom of Mokhaiotl kills", "doom_of_mokhaiotl", RAID),
    ("{threshold} Duke Sucellus kills", "duke_sucellus", BIG),
    ("{threshold} General Graardor kills", "general_graardor", BIG),
    ("{threshold} Giant Mole kills", "giant_mole", BIG),
    ("{threshold} Grotesque Guardians kills", "grotesque_guardians", BIG),
    ("{threshold} Hespori kills", "hespori", MINOR),
    ("{threshold} The Hueycoatl kills", "the_hueycoatl", BIG),
    ("{threshold} Kalphite Queen kills", "kalphite_queen", BIG),
    ("{threshold} King Black Dragon kills", "king_black_dragon", BIG),
    ("{threshold} Kraken kills", "kraken", BIG),
    ("{threshold} Kree'Arra kills", "kreearra", BIG),
    ("{threshold} K'ril Tsutsaroth kills", "kril_tsutsaroth", BIG),
    ("{threshold} Lunar Chests", "lunar_chests", (100, 250, 500, 1000)),
    ("{threshold} Mad Angel kills", "mad_angel", BIG),
    ("{threshold} Maggot King kills", "maggot_king", BIG),
    ("{threshold} Mimic kills", "mimic", RARE),
    ("{threshold} Nex kills", "nex", BIG),
    ("{threshold} Nightmare kills", "nightmare", BIG),
    ("{threshold} Phosani's Nightmare kills", "phosanis_nightmare", BIG),
    ("{threshold} Obor kills", "obor", MINOR),
    ("{threshold} Phantom Muspah kills", "phantom_muspah", BIG),
    ("{threshold} Sarachnis kills", "sarachnis", BIG),
    ("{threshold} Scorpia kills", "scorpia", BIG),
    ("{threshold} Scurrius kills", "scurrius", BIG),
    ("{threshold} Shellbane Gryphon kills", "shellbane_gryphon", BIG),
    ("{threshold} Skotizo kills", "skotizo", MINOR),
    ("{threshold} Sol Heredit kills", "sol_heredit", RARE),
    ("{threshold} Spindel kills", "spindel", BIG),
    ("{threshold} Tempoross kills", "tempoross", BIG),
    ("{threshold} The Gauntlet kills", "the_gauntlet", GAUNTLET),
    ("{threshold} The Corrupted Gauntlet kills", "the_corrupted_gauntlet",
     GAUNTLET),
    ("{threshold} The Leviathan kills", "the_leviathan", BIG),
    ("{threshold} The Royal Titans kills", "the_royal_titans", BIG),
    ("{threshold} The Whisperer kills", "the_whisperer", BIG),
    ("{threshold} Theatre Of Blood kills", "theatre_of_blood", RAID),
    ("{threshold} Theatre Of Blood (HM) kills", "theatre_of_blood_hard_mode",
     RAID),
    ("{threshold} Thermonuclear Smoke Devil kills",
     "thermonuclear_smoke_devil", BIG),
    ("{threshold} Tombs of Amascut kills", "tombs_of_amascut", RAID),
    ("{threshold} Tombs of Amascut (Expert Mode) kills",
     "tombs_of_amascut_expert", RAID),
    ("{threshold} TzKal-Zuk kills", "tzkal_zuk", RARE),
    ("{threshold} TzTok-Jad kills", "tztok_jad", MINOR),
    ("{threshold} Vardorvis kills", "vardorvis", BIG),
    ("{threshold} Venenatis kills", "venenatis", BIG),
    ("{threshold} Vet'ion kills", "vetion", BIG),
    ("{threshold} Vorkath kills", "vorkath", BIG),
    ("{threshold} Wintertodt kills", "wintertodt", BIG),
    ("{threshold} Yama kills", "yama", BIG),
    ("{threshold} Zalcano kills", "zalcano", BIG),
    ("{threshold} Zulrah kills", "zulrah", BIG),
    ("{threshold} Bounty Hunter (Hunter) score", "bounty_hunter_hunter",
     (1000, 5000, 10_000)),
    ("{threshold} Bounty Hunter (Rogue) score", "bounty_hunter_rogue",
     (1000, 5000, 10_000)),
    ("{threshold} Clue Scrolls (All)", "clue_scrolls_all", BIG),
    ("{threshold} Clue Scrolls (Beginner)", "clue_scrolls_beginner",
     (200, 500, 1000, 5000)),
    ("{threshold} Clue Scrolls (Easy)", "clue_scrolls_easy", BIG),
    ("{threshold} Clue Scrolls (Medium)", "clue_scrolls_medium", BIG),
    ("{threshold} Clue Scrolls (Hard)", "clue_scrolls_hard", BIG),
    ("{threshold} Clue Scrolls (Elite)", "clue_scrolls_elite", RAID),
    ("{threshold} Clue Scrolls (Master)", "clue_scrolls_master", RAID),
    ("{threshold} Last Man Standing score", "last_man_standing",
     (2000, 5000, 10_000, 15_000)),
    ("{threshold} Soul Wars Zeal", "soul_wars_zeal", (5000, 10_000, 20_000)),
    ("{threshold} Collections Logged", "collections_logged",
     (500, 700, 900, 1000, 1100, 1200)),
]

BASE_LEVELS = (60, 70, 80, 90, 99)


class Definition:
    def __init__(self, name, metric, measure, threshold, base=False):
        self.name = name
        self.metric = metric
        self.measure = measure
        self.threshold = threshold
        self.base = base

    def value(self, values):
        """The number this milestone is measured by, from {metric: value}."""
        if self.base:
            # Base N stats: experience summed with each skill capped at the
            # level's requirement, so it is met only when every skill is.
            cap = self.threshold / len(REAL_SKILLS)
            return int(sum(min(max(0, values.get(s) or 0), cap)
                           for s in REAL_SKILLS))
        return values.get(self.metric)

    def met(self, values):
        value = self.value(values)
        return value is not None and value >= self.threshold

    def as_row(self, achieved_at, accuracy):
        """The shape store.save_achievements takes - Wise Old Man's."""
        return {"name": self.name, "metric": self.metric,
                "measure": self.measure, "threshold": self.threshold,
                "createdAt": achieved_at, "accuracy": accuracy}


def _format_threshold(template, threshold):
    # Wise Old Man's formatAchievementThreshold, with its formatNumber.
    if threshold < 1000:
        return str(threshold)
    if threshold <= 10_000:
        return "{}k".format(_trim(threshold / 1000))
    if threshold == SKILL_EXP_AT_99:
        return "99"
    if template.startswith("Base {level} Stats"):
        # 23, not the skill count: the original divides by the pre-Sailing
        # count here, and its names are what is stored.
        return str(level_for_exp(threshold / 23))
    if threshold < 10_000_000:
        return "{}k".format(round(threshold / 1000))
    if threshold < 1_000_000_000:
        millions = threshold / 1_000_000
        return "{}m".format(_trim(millions) if millions % 1 == 0
                            else "{:.2f}".format(millions))
    billions = threshold / 1_000_000_000
    return "{}b".format(_trim(billions) if billions % 1 == 0
                        else "{:.2f}".format(billions))


def _trim(number):
    return str(int(number)) if number % 1 == 0 else str(number)


def _measure(metric):
    if metric in REAL_SKILLS or metric == "overall":
        return "experience"
    return "score" if metric in ACTIVITIES else "kills"


def definitions():
    found = []
    for level in BASE_LEVELS:
        threshold = exp_for_level(level) * len(REAL_SKILLS)
        name = "Base {level} Stats".replace(
            "{level}", _format_threshold("Base {level} Stats", threshold))
        if name == "Base 99 Stats":
            name = "Maxed Overall"
        found.append(Definition(name, "overall", "levels", threshold,
                                base=True))
    for template, metric, thresholds in TEMPLATES:
        for threshold in thresholds:
            found.append(Definition(
                template.replace("{threshold}",
                                 _format_threshold(template, threshold)),
                metric, _measure(metric), threshold))
    return found


DEFINITIONS = definitions()


def values_of(snapshot):
    """{metric: value} from a snapshot in the stored shape."""
    values = {}
    data = snapshot.get("data") or {}
    for section, key in (("skills", "experience"), ("bosses", "kills"),
                         ("activities", "score")):
        for metric, entry in (data.get(section) or {}).items():
            if isinstance(entry, dict) and entry.get(key) is not None:
                values[metric] = entry[key]
    return values


def met(values):
    """Every milestone these values meet - what Wise Old Man would list."""
    return [d for d in DEFINITIONS if d.met(values)]


def crossed(before, after, before_at, after_at):
    """Milestones met in `after` but not in `before`, as rows to store.

    `before` is None for a player's first reading, which crosses nothing.
    A milestone whose metric was not known before - a boss with no count at
    all - is not a crossing either; that is Wise Old Man's rule, and it is
    what stops the first reading after a new hiscore row appears from
    awarding everything at once. Base stats are measured across every skill
    and filed under overall, so they wait for an overall reading too.
    """
    if not before:
        return []
    accuracy = None
    if before_at and after_at:
        start, end = parse_api_time(before_at), parse_api_time(after_at)
        if start and end:
            accuracy = int((end - start).total_seconds() * 1000)
    rows = []
    for definition in DEFINITIONS:
        if before.get(definition.metric) is None:
            continue
        if not definition.met(before) and definition.met(after):
            rows.append(definition.as_row(after_at, accuracy))
    return rows
