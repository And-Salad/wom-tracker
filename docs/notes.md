# Notes

Longer-form background that the [README](../README.md) points at: why some of
this is shaped the way it is, and the traps found on the way there. None of it
is needed to run the tracker.

## Two processes, one config file

`Config.save()` does not write the object's whole in-memory snapshot. The
server holds one `Config` for as long as it runs, and a CLI job alongside it
has its own; a blind write of either snapshot would silently revert whatever
the other had written since. Reverting `last_run` is the one that bites - the scheduler
would decide a run was overdue and fire a duplicate pass over every player. So
`__setitem__` records which keys were touched, and `save()` re-reads the file
under the lock and lays only those keys over it.

Logging is split for the same reason: `RotatingFileHandler` rotates by renaming
the open file, which Windows refuses while another process holds it. The server
writes `data/wom-web.log` and CLI jobs write `wom-cli.log`, so the two never
share one.

## The palette

Dark throughout, from one definition. `wom/theme.py` holds the colours and
emits them as CSS custom properties; the page styles itself from those and
`chartkit.js` reads the same variables back for its axes, gridlines and
tooltips, so a chart can never land on a background it does not match. There is no light
mode or toggle - changing the constants in that module changes everything.

Every constant in `theme.py` has to appear in `css_variables()`, because that
dict is the only way any of them reaches the page. `--grid` was missing from
it for a while, so chartkit.js quietly drew its gridlines from the hardcoded
fallback beside the lookup and editing `GRID` did nothing at all.

### Player colours

Every player gets a colour from the default palette by their position in the
list, and that colour is used for them in every chart and every swatch. Pick a
different one on the admin page. Choices are stored in `player_colors` in
`data/config.json`, keyed by lowercase username.

### The period

The dropdown picks a rolling look-back window: day, week, month, quarter (91
days) or year. Gains run from a baseline snapshot to the newest one held.
Windows are defined in `wom/periods.py`.

Picking that baseline is not as simple as "the last snapshot before the window
opened". Wise Old Man only holds the snapshots it has, and for a player it has
not been watching long the previous one can predate the window by years - which
would report four years of kills as "this month". So `baseline_snapshot` takes
whichever snapshot sits **closer to the window edge**: the earlier one
overstates by whatever happened before the window, a later one understates by
whatever happened at the start of it, and the nearer of the two is wrong by
less. Where the gap is large enough to matter the chart says so rather than
letting it pass unremarked, captioned under the columns ("Short history:
someone from 06 Aug 2026 (25d)").

The other trap is the hiscores' unranked sentinel. The API sends `-1` for a
metric the player is not ranked on, which `_num` stores as NULL. `metric_gains`
therefore has to LEFT JOIN and treat a missing baseline as zero: an inner join
silently dropped those metrics from the window entirely, so a boss taken from
unranked to 286 kills counted as *no kills at all*, and a year could show fewer
kills than a week. Unranked means below the hiscore cutoff, so zero is the right
thing to measure from - as it is for a boss that did not exist yet when the
baseline was taken.

### The charts

**Experience gained by skill** — a stacked column per skill, one slice per
included player. All 24 skills stay on the axis whether or not they moved, so an
untrained skill reads as an empty column. Skills are in in-game skills-tab order
(`SKILL_ORDER` in `wom/icons.py`).

**Top 20 boss kills** — the same shape for kill counts, but only the twenty
bosses with the most kills across the included players this period, ranked
highest first. Which bosses appear therefore changes with the period.

**Levels over time** — one line per included player across the chosen period,
with its own **Show** dropdown: total level by default, then every skill in
alphabetical order. The snapshot taken just before the window is pulled in as a
baseline so a line starts at the left edge rather than wherever its first
snapshot inside the window happens to fall, and the axis is pinned to the window
so there is no empty space on the right. A line only starts partway across when
that player has no history before that point.

**Collection log and clues over time** — the same line-chart shape as the one
above, with its own **Show** dropdown: collection log slots by default, then
clue scrolls all-tiers and each tier individually. Plotting one metric at a time
means "all" can sit in the list without double counting the tiers under it.

The stacked charts leave out players who gained nothing, so the legend stays
honest.

Both trend charts run through one `_trend_by_metric` helper, so a new
line chart is a metric, a field to plot, and a tooltip caption.

### Chart resolution

Updates land every ten minutes, and more whenever a plugin or a manual
refresh fires — far more detail than a month-wide axis can draw. Line charts
therefore plot **one point per day** on the month, quarter and year windows,
taking each day's last reading; day and week keep every snapshot, because there
a day's worth of points *is* the chart. A year of one player collapses from 488
points to 132 without moving the line. The rule lives on each `Period`
(`bucket` in `wom/periods.py`), and `db.metric_history(..., bucket="day")`
implements it.

### Phones

The dashboard is laid out for a desktop first - a fixed sidebar beside a wide
chart column - which on a phone left the charts about two hundred pixels wide
and pushed the page into a sideways scroll. Below 820px everything stacks: the
player picker becomes a compact strip of chips above the cards, the header
wraps, and the tree indents in smaller steps.

The charts adapt too, in `chartkit.js` rather than CSS, because an SVG cannot
reflow. Below 560px they switch to tighter margins, drop the rotated axis
label (the tick numbers already carry the units), thin the legend, ask for
fewer ticks, and shorten the card - the same 360px height against a 340px
width reads worse than a wide short chart. The legend reports how many rows it
used and the plot starts below it, so a wrapped legend never lands on the top
of the plot.

Touch is handled explicitly: a finger produces no hover, so every tooltip
target also answers to `touchstart`/`touchmove`, the tooltip reads its position
off `event.touches[0]` rather than the event, and a tap anywhere outside a
chart dismisses it - a finger never fires `mouseleave`.

### Telling the recaps about the gaps

The written recaps face the same problem the charts do, and worse: a month
with no readings in it produces zeros, and a model handed zeros will say the
player did nothing. Every digest therefore opens with a `Data coverage` line -
how many readings fall inside the window, what it was actually measured from
and to, and whether the baseline sits outside it. Three cases get spelled out:

- the baseline predates the window, so the totals span the dark stretch and the
  work was *logged* when the reading landed rather than done in the window;
- the earliest reading falls well inside the window, so the start is missing;
- no pair of readings covers the window at all, which is "not measured", not
  "did nothing".

Both prompts (`data/summary_prompt.txt`, `data/group_prompt.txt`) carry matching
instructions, including telling the recap not to rank a player on a total
that covers a longer stretch than everyone else's. The coverage line is part of
the digest, so it is part of the digest hash: adding it re-dated every stored
summary, which is what forced the last regeneration.

### Windows longer than the tracking

Quarterly and yearly notes were added before either had ever been due, so the
first of each was written from whatever history existed - which for 2025 is
nothing at all for four of the six accounts. Two things make that honest
rather than misleading.

The digest gains a **nearest reading** line for a window it cannot measure:
where that account stood at the closest date either side, explicitly labelled
a landmark rather than a figure for the period. And `summary_prompt_year.txt`
and `summary_prompt_quarter.txt` - the per-period prompt files, which needed
no new code - tell the model to use that landmark to place a player and never
to interpolate between two dates and present the result as measured.

Only the per-player ones now. The group recap became the Maxing Leaderboard's
feed and covers the day and the month alone, so the group counterparts of
these files were describing windows the calendar has no verdict for; the
recaps written for them were dropped and the prompts are no longer offered.

It works: the 2025 note for an account that was not being tracked then opens
by saying it "wasn't tracked at all during 2025", quotes the landmark reading
it does have, and says in as many words that this "is just a reference point,
not a measure of what happened in 2025".

### Missing history

Wise Old Man's snapshot history has holes - for a player it has not watched
closely, weeks or months with nothing at all. A line chart joining across one
draws a straight segment through time nobody measured, which reads as steady
progress that may never have happened. On a real Year view here, six of one
player's nineteen points sat 24 to 86 days apart, so most of that line was
invented.

Those stretches are dashed. The ends of a dashed segment are real readings; the
middle is interpolation. Anything closer together than 4% of the window
(floored at a day and a half, so short windows never dash) is drawn solid, in
`Chart.prototype.trend` in `chartkit.js`.

## Milestones

A feed of every achievement Wise Old Man has recorded — 99s, base-stat
milestones, kill-count and XP thresholds — newest first, one row per milestone,
coloured by player and labelled with the same icons the charts use.

The **Since** dropdown filters to a rolling window or shows all time, and the
feed only lists the players included by the sidebar swatches, so it narrows the
same way the Overview page does.

Two quirks of the API worth knowing, both handled in the display:

- Wise Old Man records how precisely it knows each date. Anything vaguer than a
  day is shown with a leading `~`, because a milestone reconstructed from
  imported history can be off by months.
- A milestone it cannot place at all comes back dated to the epoch. Those read
  **unknown** and sort to the bottom rather than claiming to have happened in
  1970.

Milestones are fetched once per player per update pass from
`GET /players/{username}/achievements`, which returns a player's whole list, so
the first pass fills in their back catalogue and later ones only add what is
new. Failures here never fail the update.

### Icons

Chart axes are labelled with the same icons RuneLite's hiscore lookup panel
shows. They live in `assets/<kind>/<metric>.png`, named after the Wise Old Man
metric so they can be looked up straight from the database. Download or refresh
them with:

```bash
py fetch_icons.py            # both kinds
py fetch_icons.py --skills   # or just one
```

Two sources, because RuneLite only ships one of them as files:

- **Skills** are RuneLite's own `skill_icons` resources — the 25×25 interface
  icons beside each level in its panel. RuneLite calls the skill `runecraft`
  where Wise Old Man says `runecrafting`, which is the one name mapped by hand
  (`RUNELITE_SKILL_FILES` in `wom/icons.py`).
- **Bosses and activities** come from Wise Old Man's metric icons at
  `wiseoldman.net/img/metrics/<metric>.png`. RuneLite renders its boss column
  from game-cache sprites (`SpriteID.IconBoss25x25`) rather than files in its
  repository, so there is nothing to download there; Wise Old Man serves those
  same hiscore sprites as PNGs, already named by metric. All 86 boss and
  activity metrics resolve.

Because these are small pixel sprites, they are padded onto a common
`ICON_CANVAS_PX` square so tall and wide icons share a baseline, then drawn at
native size with nearest-neighbour sampling — smooth resampling turns them to
mush. When a chart gets too narrow for one icon per column they step down to
3/4 or 1/2 size rather than overlapping.

If the icons are missing the charts fall back to text labels.

## Historic data

The first time a player is stored, the pass also imports whatever history Wise
Old Man already holds for them, via `GET /players/{username}/snapshots` paged
back to 2013. So a name added today arrives with months of real history instead
of a single point, and the time-series charts are useful immediately.

This happens automatically — adding a username under Options is all it takes.
The import is marked in `players.backfilled_at` and never repeats; later passes
only add the new snapshot. If the history call fails, the update still succeeds
and the reason is noted against that player.

Very long histories are capped at 5,000 snapshots (`SNAPSHOT_MAX_PAGES` in
`wom/api.py`). Pages come back newest first, so a cap drops the oldest end and
says so in the run notes.

To pull history again after clearing the database:

```bash
py wom_tracker.py --backfill
```

Name players after the flag to redo just those. Duplicate snapshots are ignored
on insert, so re-running is harmless.

## Adding a chart

An Overview chart is described once and drawn once. Describe it in
`wom/catalog.py`:

```python
ChartSpec("boss_gains", "Boss kills gained", "stacked",
          description="Shown under the chart title.")
```

`kind` is `stacked` or `trend`, and `options=[...]` gives it a dropdown whose
selection arrives as `ctx.choice`. Then build its data in `wom/web/data.py`:

```python
def _boss_gains(ctx, _choice):
    return _stacked(ctx, "boss", ranked_metrics, "Kills gained", "kills gained",
                    "No boss kills by the included players in the last {}.")

_BUILDERS = {..., "boss_gains": _boss_gains}
```

`ctx` is a `ViewContext` (`wom/context.py`) carrying `db`, `config`, the
included `selected` players and the `period`. `ctx.gains(player, kind)` returns
`{metric: gained}` and memoises, so ask freely rather than repeating the
snapshot lookups; `ctx.db.metric_history(player_id, metric, kind, since=...)`
gives a time series with its baseline point.

If it is a stacked or trend chart, that is all - `chartkit.js` already draws both
shapes, icons, legend, tooltips and the responsive behaviour. A genuinely new
shape needs a branch in `Chart.prototype.draw` and a drawing function beside
`stacked` and `trend`.
