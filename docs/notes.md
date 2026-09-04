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

### Selections, and where they are kept

Every control on the site is a choice about what somebody wanted to see, and
each one was thrown away on the next visit. The sidebar came back with every
account ticked over the last week, the Milestones kinds came back all on, and
each Overview card came back on the first entry of its dropdown. A tracker is
a page people open every morning, so that was the same handful of clicks every
morning.

They are kept in `localStorage` now, by `static/store.js` - a wrapper over
about six lines, but the wrapping is the point. Reading `window.localStorage`
at all throws in a browser set to block site data, so every call is guarded and
a failure is silent: a reader in a private window still gets the page the
server rendered. Nothing stored is anything the page could not do without.

**A URL beats it, always.** The sidebar restores only when there is no query
string. A link that names players and a period says so in as many words - it is
how the tabs carry the sidebar between each other and how a view is shared -
and quietly overruling it with something out of one reader's browser would
make a pasted link mean two different things to the two people reading it.

The awkward half is what to do about the page *behind* a restored sidebar,
which the server has already rendered from the bare URL. A page that refetches
is told the usual way, through `Sidebar.onChange`. One that does not - Recaps
is server-rendered text and says so on the form, and the Gallery's panels have
no listener at all - has to be asked for again, or the ticks and the screen
disagree. Which of the two a page is only becomes knowable once it has
registered its listener, and a page does that inside its own
`DOMContentLoaded` handler; `sidebar.js` runs first, so its handler runs first
too. Hence `whenReady`: a timer set *from* that handler, which is the earliest
moment all of them have run. Asked any sooner, every page looks like one that
cannot refetch and every restored visit costs a second render.

`Sidebar.restored` is the other half of that. The three pages that fetch their
own opening copy - Overview, Maxing's trend, the Data table - skip it when the
sidebar came back from the browser, because the round of requests it would
make is the one already on its way.

Two smaller decisions worth naming. The kind filters store the kinds turned
*off*, not the ones left on, so a category added later starts visible rather
than being hidden by a list written before it existed. And an Overview card's
mode is restored by pressing the button, not by telling the chart: `Chart`
reads its mode off `aria-pressed` when it is constructed, so moving the button
first is the whole of it - and the card is then drawn once, on what was asked
for, rather than drawn on the default and immediately fetched again.

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

Both trend charts run through one `_trend` helper, so a new line chart is a
metric, a field to plot, and a tooltip caption.

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

Nothing is done to them on the way in: `fetch_icons.py` writes the bytes it
downloaded and that is the whole of it. The handling that keeps them legible
is two attributes in the browser, in `Chart.prototype.stacked`:
`image-rendering: pixelated` so a scaled sprite stays crisp rather than being
smoothed to mush, and `preserveAspectRatio="xMidYMid meet"` so tall and wide
icons share a baseline inside their square. The square itself is
`Math.min(ICON_PX, x.bandwidth())` - a continuous shrink as the columns
narrow, not a step down through fixed sizes.

(This paragraph used to describe a server-side pipeline: padding onto an
`ICON_CANVAS_PX` canvas and resampling before saving. That was true when the
charts were drawn with matplotlib. There is no image library on the server any
more, and there was no constant by that name to point at.)

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

## When a session actually happened

Every gain on this dashboard is stamped with the moment we *noticed* it, which
is not the moment it was earned. The hiscores do not move while someone is
logged in, so three hours of training becomes one jump, attributed to whichever
ten-minute window our next poll fell in. Around one gain in eighteen lands on
the wrong side of a local midnight because of it.

Two things narrow that, and it is worth writing down which is which.

### What we already had

`snapshots.captured_at` is Wise Old Man's `createdAt`, not our poll clock (see
`save_snapshot`). Wise Old Man only creates a snapshot when the data actually
differs, so the stamp belongs to whoever first observed the change - and we are
not the only one asking. RuneLite ships **XP Updater**, which POSTs to the same
`/players/{username}` endpoint we do on logout and on world hop, whenever at
least 10,000 experience was gained.

That leaves a signature in our own data. Each account's polls land on a fixed
ten-minute phase; readings more than 90 seconds off it were triggered by
someone else. Over 30 days, excluding every reading traceable to one of our own
runs:

```
on our polling grid : 153 of 2180 carry a gain   (7%)
off the grid        : 108 of  114 carry a gain  (95%)
```

Thirteen times more likely, which is exactly what a signal that fires on logout
and only when something was gained looks like. Of those 108, 102 are followed
by twenty minutes of quiet - genuine session ends rather than world hops.

Two consequences. Hiscore propagation is fast: a push at the logout screen
already sees the new experience, so our own detection brackets a logout by the
poll interval with no systematic lag hiding under it. And the 10,000 threshold
means quiet sessions never push at all, so this is a precision upgrade on
sessions we would have caught anyway, not extra coverage.

### What was missing

The start, and an end that is a moment rather than a bracket. Neither comes out
of the Wise Old Man pipeline, which is what `wom/web/hooks.py` and the
`session_events` table are for. Dink reports a login as it happens with that
account's live experience attached, and a logout carrying only the moment - but
carrying it however little was gained, where the 10,000 threshold above leaves
quiet sessions invisible. A session then has a measured beginning and a measured
end instead of one point and a guess.

`metrics.efficiency` (EHP) remains the only estimate of how long a session ran,
and stays useful for placing activity inside a span - someone can log in, idle
an hour, then train.

### Before any of this is used

`compact_snapshots` thins beyond 30 days to one reading a day, which used to
discard every reading Wise Old Man had taken without us - the ones that record
a moment our ten minute rhythm could never have caught. It now keeps them, and
which kind a reading is stopped being an inference: `snapshots.origin` records
it as the reading is stored, from whether the stamp Wise Old Man gave it was a
moment before or well before we collected it.

Note what that correction cost. The phase test above tells you a reading was
not taken on our rhythm; it does not tell you who took it. Most of the readings
it flags are imported history, which is why so many of them carry a change -
Wise Old Man only makes a snapshot when something moved, so an archive of them
is a list of changes by construction. `origin` answers the question the phase
test only appeared to.


### Which span a gain belongs to

`wom/sessions.py` turns a recorded gain back into the stretch of time it was
earned in. `resolve(previous_at, reading_at, events)` returns a `Span` with
both ends labelled `measured` or `inferred`, so a caller can tell what it is
being given.

The start, in order of preference:

1. the first login between the two readings - the *first*, because two short
   sessions inside one polling interval arrive as one gain, and taking the
   later login would date all of it from the last few minutes;
2. a login from before the previous reading that was never closed. This is the
   case the whole exercise exists for: four hours of training crosses several
   readings without moving any of them, because the hiscores are frozen until
   logout, and that login is the only record of when it began. A span that
   starts before the previous reading is therefore correct, not a bug;
3. the previous reading - exactly what the app did before Dink existed.

The end is the last logout between the two readings, or the reading itself.

A login with no logout stops counting after `MAX_SESSION_HOURS`, or a client
left running overnight would turn a ten minute gain into a sixteen hour
session and smear it across two days.

### Crediting it to the right day

Every windowed total in the app is a state difference: `metric_gains` and
`winners.measure` both read the value at two moments and subtract. Neither
ever sees an individual gain, so neither can divide one across a boundary, and
teaching them to would mean rewriting the two functions the Overview, the
recaps, the charts and the leaderboard all stand on.

So the division is written into the data instead. When a known span crosses a
local midnight, `sessions.attribute` records what the account had earned by
that midnight, and every total downstream becomes right without changing a
line of it - they all read `state_at`, and `state_at` now has an answer there.

Three things make that fit:

- A span crosses at most one boundary, because `MAX_SESSION_HOURS` is under a
  day - and week, month, quarter and year boundaries are all local midnights,
  so one interpolation covers every window the app draws.
- It fills a gap rather than contradicting a reading. A session leaves no
  metric rows behind it at all: the hiscores did not move, so nothing was
  stored. The interpolated values go where there was nothing.
- An account with no session events produces nothing and is left byte for
  byte as it was, which is the whole of the fallback.

The rows are marked `derived` so compaction keeps them and nothing mistakes
them for something Wise Old Man said, and each run clears and recomputes the
last few days so a late logout corrects rather than accumulates.

The interpolation is linear in time, which is wrong for someone who logs in,
idles an hour and then trains. It is still far closer than crediting a
four-hour session to the minute it ended. EHP could weight it later.

Worth knowing what this is worth: only 1% of experience lands within ten
minutes of a local midnight, which is all that point attribution could ever
get wrong - but 32% lands within three hours of one, which is the band this
moves.


### What arrives while somebody is playing

`wom/gameplay.py` handles the opt-in half of the webhook. Three of Dink's
notifications say something we can use, and each does two jobs.

They are stored whole in `game_events`, because the detail is the point and
none of it fits the metrics table - a collection log feed wants the item, the
drop it came from and the rank it completed. And where the payload happens to
*be* a metric we already track, the value is written at the moment it happened
as a `reported` reading, so the charts stop rounding it to the next poll.

| notification | kept | written through as | on the feed |
|---|---|---|---|
| `COLLECTION` | item, dropper, rank, counts | `activity/collections_logged` | yes |
| `KILL_COUNT` | boss, count, kill time, personal best | `boss/<metric>` | no |
| `LEVEL` | one row per skill that levelled | nothing, see below | no |
| `QUEST` | quest name, quests completed | nothing we track | yes |
| `ACHIEVEMENT_DIARY` | area and difficulty together | nothing we track | yes |
| `COMBAT_ACHIEVEMENT` | task, tier, points | nothing we track | yes |

`FEED_KINDS` decides the last column, and the rule behind it is that the feed
is for things somebody did once and would mention. A level or a boss count is
progress and belongs on a chart; a quest is an event. A diary is stored as
"Varrock Hard" rather than as an area and a difficulty, because neither half
names a diary on its own.

The feed merges those with Wise Old Man's milestones and sorts the two together
by date, with anything Wise Old Man could not date at all last - an undated
milestone is not news, and on top it would push out what happened today. Every
row carries its kind so the filter above the table can hide one, including the
rows redrawn from `/api/milestones` when the sidebar changes.

Levels are not written through. Our level total lives in the `level` column of
the `overall` row, beside overall experience in `value`, and a level reported
without experience would be a row that reads as authoritative while carrying
half an answer. Worth doing deliberately rather than as a side effect.

Boss names arrive as display names and have to become metric names. The
transform drops apostrophes rather than replacing them - Wise Old Man writes
Kree'arra as `kreearra` - and the result is checked against metrics we already
store rather than trusted, because the name comes from a plugin and a wrong
guess would invent a boss. The article is tried both ways, since Wise Old Man
keeps it for `the_whisperer` and drops it for `nightmare` with no rule in it.
A name that does not resolve keeps the event and writes no reading.

Experience cannot be streamed at all. Dink's `XP_MILESTONE` fires only for
skills already at level 99, at intervals of a million experience or more, so
it is a milestone rather than a measurement.


### Pictures

`wom/gallery.py` is the only place the app takes arbitrary bytes off the
internet, so most of it is about refusing them.

Images are accepted for deaths and pets and for nothing else. Dink can attach
one to most of its notifications, but a level-up screenshot is not something
anybody would go and look at, and every kind we accept is more that a leaked
URL can push at us.

Three rules make the rest safe. The format is read from the first bytes of the
file, never from the content type - that is the one part of the request nobody
had to prove, and a mislabelled file served back with the label it claimed is
how a picture becomes a script. The file is named by the sha256 of its own
contents, so nothing a client sends is ever used as a path and the same
screenshot delivered twice is stored once. And the route checks the digest
against the database before it touches the filesystem, serving the type we
recorded rather than the one that was asked for.

They sit on the volume, not in the database. A few hundred megabytes of PNG
inside `wom.db` would ride along on every backup pull for something decorative,
and losing a picture costs a picture where losing a snapshot costs history that
cannot be fetched again. `backup.py` does not carry them, deliberately.

Forty of each kind are kept - the page shows ten, and the rest are held so a
feed can look further back without anyone having to play again first - under a
250 MB ceiling as a backstop, on a volume of one gigabyte holding a database of
about two megabytes. The count is the usual limit; the byte budget exists for
the case where a run of enormous screenshots stays under the count and over the
disk.


### Opening a picture

Each thumbnail is a `<button>` wrapping the image, not an image with a click
handler on it. Opening a picture is something you can do, so it answers the
keyboard and announces itself without any help from us; the button keeps its
focus ring and none of a button's other appearance.

The viewer is one `<dialog>` for the whole page, filled in on click. It closes
three ways - the close button, a click on the backdrop, and Escape - and the
third is handled in the script rather than left to the browser. Escape is
supposed to close a modal dialog on its own; in at least one browser the key
arrives at the page and the dialog stays open. That was found by checking, not
by reading, and it is why the listener is there.

Closing releases the image's `src`. Ten screenshots is already a lot for a
browser to hold and there is no reason to keep an eleventh copy decoded.
