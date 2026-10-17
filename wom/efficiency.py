"""Efficient hours played and bossed, worked out here rather than asked for.

A port of Wise Old Man's EfficiencyAlgorithm (server/src/api/modules/
efficiency/EfficiencyAlgorithm.ts, MIT licensed, (c) Wise Old Man). It is a
port rather than a reinterpretation on purpose: the numbers are only worth
showing if they are the ones people compare on Wise Old Man, so the bonus
experience ordering, the three scaled bonuses and the rounding all follow the
original line for line, and tests/test_efficiency.py holds it to Wise Old
Man's own answers for real accounts.

The rates are Wise Old Man's too, read from its public rates endpoint - a
read, not an update - and cached in the database, so they follow its changes
without anybody editing a table here, and an outage keeps the last ones.
"""

MAX_SKILL_EXP = 200_000_000
SKILL_EXP_AT_99 = 13_034_431

# Wise Old Man's skill order, which is also the hiscores'. The bonus
# calculation sorts with a stable sort, so the order is part of the answer.
REAL_SKILLS = (
    "attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic",
    "cooking", "woodcutting", "fletching", "fishing", "firemaking", "crafting",
    "smithing", "mining", "herblore", "agility", "thieving", "slayer",
    "farming", "runecrafting", "hunter", "construction", "sailing",
)

# Account type -> the rate table Wise Old Man applies to it. Builds (f2p,
# level 3, 1 defence) are not tracked here; every account reads as a main
# build, which is what Wise Old Man reports for all of ours.
ALGORITHM_TYPES = {
    "regular": "main", "unknown": "main",
    "ironman": "ironman", "hardcore": "ironman",
    "ultimate": "ultimate",
}


def algorithm_type(player_type):
    return ALGORITHM_TYPES.get((player_type or "").lower(), "main")


def _round(value, places=5):
    # roundNumber in the original: Math.round(n * 10^p) / 10^p, which rounds
    # halves up where Python's round() goes to even.
    factor = 10 ** places
    return int(value * factor + 0.5) / factor if value >= 0 else \
        -int(-value * factor + 0.5) / factor


class Algorithm:
    """One account type's rates, and everything worked out from them."""

    def __init__(self, skill_metas, boss_metas=None):
        self.skill_metas = list(skill_metas or ())
        self.boss_metas = list(boss_metas or ())
        self._methods = {m["skill"]: m.get("methods") or []
                         for m in self.skill_metas}
        self.start_bonuses = self._bonuses(end=False)
        self.end_bonuses = self._bonuses(end=True)
        self.direction = self._direction(self.start_bonuses + self.end_bonuses)
        self.maximum = self._maximum_map()

    # -- public ------------------------------------------------------------

    def ehp_map(self, stats):
        """{skill: hours, ..., "overall": total} for {skill: experience}."""
        fixed = {s: max(0, stats.get(s) or 0) for s in REAL_SKILLS}
        result = {"overall": 0}
        start_bonus = self._bonus_exp(fixed, end=False)
        end_bonus = self._bonus_exp(fixed, end=True)

        for origin in REAL_SKILLS:
            total = 0.0
            bonus_skills = list(dict.fromkeys(self.direction.get(origin, [])))
            # A bonus skill's own bonus skills are affected too (woodcutting
            # feeds firemaking, which feeds cooking), so they count as well.
            # The original adds to the Set it is iterating, and a JavaScript
            # Set visits what is added mid-iteration - so it follows the
            # chain all the way, and so does walking a growing list.
            index = 0
            while index < len(bonus_skills):
                for dependant in self.direction.get(bonus_skills[index], []):
                    if dependant not in bonus_skills:
                        bonus_skills.append(dependant)
                index += 1

            for skill in bonus_skills + [origin]:
                start = fixed[skill] + start_bonus[skill]
                end = MAX_SKILL_EXP - end_bonus[skill]
                if end - start <= 0 and skill != origin:
                    continue
                reset = dict(fixed)
                reset[origin] = 0
                start_reset_bonus = self._bonus_exp(reset, end=False)
                end_reset_bonus = self._bonus_exp(reset, end=True)
                start_reset = reset[skill] + start_reset_bonus[skill]
                end_reset = MAX_SKILL_EXP - end_reset_bonus[skill]
                diff = (self._skill_time(skill, start_reset, end_reset)
                        - self._skill_time(skill, start, end))
                if end - start <= 0:
                    total += min(self.maximum[skill], diff)
                else:
                    total += diff
            result[origin] = _round(total)

        result["overall"] = sum(v for k, v in result.items() if k != "overall")
        return result

    def ehb_map(self, kills):
        """{boss: hours} for {boss: kills}; bosses without a rate are 0."""
        result = {}
        for meta in self.boss_metas:
            rate = meta.get("rate") or 0
            if rate <= 0:
                continue
            result[meta["boss"]] = _round(max(0, kills.get(meta["boss"]) or 0)
                                          / rate)
        return result

    def ehp(self, stats):
        return self.ehp_map(stats)["overall"]

    def ehb(self, kills):
        return sum(self.ehb_map(kills).values())

    def ttm(self, stats):
        """Hours from here to level 99 in everything."""
        maxed = dict.fromkeys(REAL_SKILLS, SKILL_EXP_AT_99)
        capped = {s: min(max(0, stats.get(s) or 0), SKILL_EXP_AT_99)
                  for s in REAL_SKILLS}
        return self.ehp(maxed) - self.ehp(capped)

    # -- the original's private methods ----------------------------------

    def _bonuses(self, end):
        found = []
        for meta in self.skill_metas:
            for bonus in meta.get("bonuses") or ():
                if bonus and bool(bonus.get("end")) == end:
                    found.append(bonus)
        return found

    @staticmethod
    def _direction(bonuses):
        direction = {}
        for b in bonuses:
            listed = direction.setdefault(b["originSkill"], [])
            if b["bonusSkill"] not in listed:
                listed.append(b["bonusSkill"])
        return direction

    def _bonus_exp(self, stats, end):
        bonuses = self.end_bonuses if end else self.start_bonuses
        result = dict.fromkeys(REAL_SKILLS, 0)
        # Skills that receive no bonus themselves go first (slayer, say), so
        # what they hand on is settled before anything depending on it.
        ordered = sorted(bonuses, key=lambda b: -len(
            self.direction.get(b["bonusSkill"], [])))
        for b in ordered:
            origin, target = b["originSkill"], b["bonusSkill"]
            if end:
                scaled = self._scaled(stats, origin, target)
                if scaled:
                    result[target] = result[target] + scaled
                    continue
            cap = min(b["endExp"], MAX_SKILL_EXP)
            origin_start = (max(stats[origin], b["startExp"])
                            + (0 if end else result[origin]))
            origin_end = cap - result[origin] if end else cap
            to_apply = max(0, origin_end - origin_start) * b["ratio"]
            result[target] = min(MAX_SKILL_EXP, result[target] + to_apply)
        return result

    # The three bonuses that scale rather than run at a fixed ratio: drift
    # net fishing (hunter -> fishing), swimming thieving (thieving ->
    # agility) and firefacts (firemaking -> thieving).
    _SCALED = {("hunter", "fishing"), ("thieving", "agility"),
               ("firemaking", "thieving")}

    def _scaled(self, stats, origin, target):
        if (origin, target) not in self._SCALED:
            return 0
        origin_meta = next((m for m in self.skill_metas
                            if m["skill"] == origin), None)
        target_meta = next((m for m in self.skill_metas
                            if m["skill"] == target), None)
        origin_method = next((m for m in (origin_meta or {}).get("methods", ())
                              if m.get("realRate")), None)
        target_methods = (target_meta or {}).get("methods") or []
        target_method = target_methods[-1] if target_methods else None
        bonuses = (origin_meta or {}).get("bonuses") or []
        ratio = bonuses[0].get("ratio") if bonuses and bonuses[0] else None
        if not origin_method or not target_method or not ratio:
            return 0

        origin_start = max(origin_method["startExp"], stats[origin])
        origin_left = MAX_SKILL_EXP - origin_start
        real = (self._skill_time(origin, origin_start, MAX_SKILL_EXP, True)
                + self._skill_time(target, stats[target], MAX_SKILL_EXP, True))
        fake = (self._skill_time(origin, origin_start, MAX_SKILL_EXP, False)
                + self._skill_time(target, stats[target], MAX_SKILL_EXP, False))
        excess = (real - fake) * target_method["rate"]
        return origin_left * ratio - excess

    def _skill_time(self, skill, start, end, real=False):
        methods = self._methods.get(skill)
        # Skills trained for free alongside others (hitpoints, magic,
        # fletching) cost a nominal sliver rather than nothing.
        if not methods or (len(methods) == 1 and methods[0]["rate"] == 0):
            return (end - start) / MAX_SKILL_EXP
        hours = 0.0
        for i, current in enumerate(methods):
            following = methods[i + 1] if i + 1 < len(methods) else None
            if current["rate"] == 0:
                continue
            rate = (current["realRate"] if real and current.get("realRate")
                    else current["rate"])
            if (following and following["startExp"] > start
                    and current["startExp"] < end):
                gained = (min(following["startExp"], end)
                          - max(start, current["startExp"]))
                hours += gained / rate
            if not following and end > current["startExp"]:
                gained = end - max(current["startExp"], start)
                hours += gained / rate
        return hours

    def _maximum_map(self):
        zero = dict.fromkeys(REAL_SKILLS, 0)
        start_bonus = self._bonus_exp(zero, end=False)
        end_bonus = self._bonus_exp(zero, end=True)
        result = {}
        for skill in REAL_SKILLS:
            result[skill] = self._skill_time(
                skill, zero[skill] + start_bonus[skill],
                MAX_SKILL_EXP - end_bonus[skill])
        result["overall"] = sum(result.values())
        return result


def annotate(snapshot, algorithm):
    """Write per-metric and total efficiency into a snapshot, in place.

    The same places Wise Old Man's snapshots carried them - `ehp` on each
    skill, `ehb` on each boss, and a `computed` section - so the readings
    stored from here look like every reading stored before, and the store's
    change test sees the same efficiency it saw last time rather than a
    missing one on every metric. Returns (ehp, ehb, ttm), or Nones when there
    are no rates to work from.
    """
    if algorithm is None or not algorithm.skill_metas:
        return None, None, None
    data = snapshot["data"]
    stats = {s: (e.get("experience") or 0) for s, e in data["skills"].items()}
    kills = {b: (e.get("kills") or 0) for b, e in data["bosses"].items()}
    ehp_by_skill = algorithm.ehp_map(stats)
    ehb_by_boss = algorithm.ehb_map(kills)
    for skill, entry in data["skills"].items():
        if skill in ehp_by_skill:
            entry["ehp"] = ehp_by_skill[skill]
    for boss, entry in data["bosses"].items():
        entry["ehb"] = ehb_by_boss.get(boss, 0)
    ehp = ehp_by_skill["overall"]
    ehb = sum(ehb_by_boss.values())
    data["computed"] = {
        "ehp": {"metric": "ehp", "value": ehp},
        "ehb": {"metric": "ehb", "value": ehb},
    }
    return ehp, ehb, algorithm.ttm(stats)


# Built Algorithms by (type, when its rates were stored). Building one works
# out the maximum hours for every skill, which is not free, and the rates
# change a few times a year.
_built = {}


def algorithm_for(database, player_type):
    """The Algorithm for this account type from the stored rates, or None."""
    kind = algorithm_type(player_type)
    ehp_metas, ehb_metas, fetched_at = database.efficiency_rates(kind)
    if not ehp_metas:
        return None
    key = (kind, fetched_at)
    if key not in _built:
        _built.clear()
        _built[key] = Algorithm(ehp_metas, ehb_metas)
    return _built[key]
