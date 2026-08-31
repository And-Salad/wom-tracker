# WOM Tracker

A web app that keeps a list of Old School RuneScape accounts updated from Wise
Old Man every six hours, charts what changed, and writes short summaries of
each period with the Claude API. One process serves the pages and runs the
schedule; the charts are drawn in the browser with D3.

Built against the [Wise Old Man v2 API](https://docs.wiseoldman.net/api).

## Running it

```bash
py web_app.py --with-scheduler
```

That serves the dashboard on <http://localhost:8000> and runs the six-hourly
updates and the summaries from the same process. Without `--with-scheduler` it
only serves what is already stored, which is what you want if something else is
doing the updating.

The public pages are read-only. Everything that changes anything - the tracked
player list, the API keys, the colours, the prompts, and buttons to run an
update or a round of summaries now rather than on the schedule - lives under
`/admin`, behind a password:

```bash
WOM_ADMIN_PASSWORD=something-long py web_app.py --with-scheduler
```

Without that variable the admin routes are not registered at all, so a
deployment that forgets to set one has no admin pages rather than open ones.

### Hosted

`Dockerfile` and `fly.toml` deploy the same thing to [Fly.io](https://fly.io),
which is where it is meant to live: a machine that is always on, so the
schedule keeps running without a PC left awake. The volume mounts at `/data`
and holds the database, the config and the prompts; the keys arrive as secrets
(`WOM_ADMIN_PASSWORD`, `WOM_SECRET_KEY`, `ANTHROPIC_API_KEY`) and never touch
the volume. `auto_stop_machines` is off on purpose - a stopped machine cannot
wake itself to run an update.

```bash
fly deploy
```

### Maintenance jobs

```bash
py wom_tracker.py --update      # one update pass now
py wom_tracker.py --summarize   # write whatever summaries are owed
py wom_tracker.py --compact     # thin old history to one snapshot a day
py wom_tracker.py --list        # print the tracked usernames
```

The server does the first two on its own; these are the same jobs by hand.
Against a hosted deployment, run them where the database is:

```bash
fly ssh console -a wom-tracker -C "python /app/wom_tracker.py --compact"
```

## Written summaries

An optional **Summaries** tab: a few paragraphs per window, written by Claude
from the numbers already in the database, in an expanding tree - the branch,
then Daily/Weekly/Monthly, then each dated window. The web dashboard shows the
same tree at `/summaries`.

The first branch is **Group**: a three-paragraph round-up of the whole roster
for that window, naming a winner and saying what it judged on, then the notable
things, then comparisons. It is built from every player's figures side by side
rather than from their individual write-ups, so comparisons rest on the numbers
and the round-up does not depend on the individual notes existing first. It
always covers all tracked players, not the sidebar selection, because there is
one stored round-up per window.

```bash
py wom_tracker.py --summarize --dry-run     # show the digest and the cost, send nothing
py wom_tracker.py --summarize               # write today's
py wom_tracker.py --summarize --due         # write exactly what the schedule owes now
py wom_tracker.py --show-prompt             # print the prompt and its path
```

Tune the prompt, re-run with `--force`, repeat. `--player NAME` and
`--period KEY` narrow it while you iterate.

**The prompt files.** `data/summary_prompt.txt` (per player) and
`data/group_prompt.txt` (the round-up) are created on first use and each covers
every period. To give one period its own instructions - a daily note and
a monthly retrospective usually want different ones - drop a file beside it
named for that period:

```
data/summary_prompt.txt          per player - the base
data/summary_prompt_day.txt      optional, overrides the base for the day
data/summary_prompt_week.txt     optional
data/summary_prompt_month.txt    optional

data/group_prompt.txt            the group round-up - the base
data/group_prompt_day.txt        optional, same override rule
data/group_prompt_week.txt       optional
data/group_prompt_month.txt      optional
```

Only the base is required; the rest are opt-in, and deleting one falls straight
back to the base. `--show-prompt` lists which file each period resolves to.

Everything Claude sees is a compact digest built in `wom/summaries.py` - about
300 tokens of XP by skill, levels gained, boss kills, activities and milestones
for that window. The prompt is the only part worth tuning; the digest is just
the data.

**This bills the Claude API, which is separate from a Claude.ai subscription.**
It is opt-in (`summaries_enabled`, off by default) and needs a key from
console.anthropic.com, set under Options or as `ANTHROPIC_API_KEY`. On the
default Sonnet 5 at low effort a summary is about half a cent, so six players
once a day is roughly a dollar a month. The model is a dropdown in Options.

### Windows, not rolling periods

Charts ask for "the last 7 days" from right now. A written summary needs the
opposite: a closed span with a name a person can read. Each one therefore
covers a **calendar window**, anchored to Eastern midnight:

| Period | Covers | Labelled |
| --- | --- | --- |
| Day | the last complete day | `Saturday 29 August 2026` |
| Week | the last complete Monday-Sunday | `17-23 August 2026` |
| Month | the last complete month | `July 2026` |

Only *finished* windows are written, so on a Sunday the newest weekly is still
the week before last. Each is stored under its own key, so they accumulate into
a history rather than overwriting each other.

### When they get written

The **6am Eastern** update slot writes any window that has closed and has not
been summarised yet - the day every morning, the week once a Monday has passed,
the month once the 1st has. Because the test is "has this window been written",
a machine asleep through a Monday still writes that week when it wakes, and a
long gap catches up rather than skipping. The other three update slots write
nothing, and `--due` runs exactly what is owed right now.

That is 430 windows a year - so 430 group round-ups plus 430 per player, about
1.18 windows a day.

Two things keep the bill down. Each summary stores a hash of its digest, so a
player whose numbers have not moved is skipped rather than re-billed - `--force`
overrides. And the calendar rules mean the six-hourly update pass writes nothing
at all outside the morning slot.

## The pages

Five public pages - **Summary** (the charts), **Milestones**, **Summaries**,
**Players** and **Data** - with the player and period filters in the sidebar,
plus **Admin** behind the password.

**Players** lists the latest figures, and opens: each player expands into
skills, bosses and activities, every row carrying the current value, the level
or rank, and what it gained over the chosen period, sorted so whatever moved is
at the top. If the readings cover less of the window than the period asks for,
it says so - the same distinction the charts and the summaries make, because a
week nobody measured otherwise reads exactly like a quiet week. That detail is fetched when a row is opened rather than rendered
with the page - every player's worth of it is a few hundred kilobytes nobody
has asked to see - and refetched if the period changes underneath it.

**Data** exports the stored readings as CSV or JSON: one row per metric per
reading, filtered by player, by kind, and by date. The dates mean the viewer's
days, not UTC ones - the page sends its offset, because otherwise an Eastern
viewer asking for "to 30 August" would stop at 20:00 their time and lose that
day's last reading. A date that will not parse is refused with a 400 rather
than ignored, since ignoring it exports the whole history while looking
filtered. Exports are budgeted, because a full one is about 5 MB of egress and walks
every stored reading on a machine that also runs the schedule: five per
address per six hours, and twenty a day across everyone as the backstop, which
is roughly 100 MB a day at today's size. Signing in as admin skips both.

The chart and player endpoints carry a much higher ceiling - 600 per address
per five minutes, against a heavy human session of a couple of hundred and a
scripted client that managed 8,400 - plus a tripwire on the total across
everyone. Past 3,000 in five minutes it latches: the data endpoints answer 503
until an admin presses **Resume serving data**. That is deliberately a stop
rather than a slowdown, so an abusive run costs one burst instead of hours of
billed traffic. The trade is real and worth knowing: whoever trips it takes
the data offline for everyone until you clear it, which is why the threshold
sits far above anything people produce. The pages, the schedule and the admin
page keep working throughout, and an admin can still read the data while it is
tripped - otherwise clearing it would mean working blind.

That budgeting only works because the client address is read from a header the
proxy sets: behind Fly's proxy every
request arrives from an internal address, so "per address" was one shared
bucket - six bad admin sign-ins from anyone locked out everyone, which is a
better denial of service than the brute force it was meant to stop. `ProxyFix`
is not enough on its own: it reads the rightmost `X-Forwarded-For` entry, which
is the proxy's own hop, and waitress strips those headers anyway unless told to
trust a proxy. `Fly-Client-IP` is set by the proxy, is not a forwarded header,
and survives. `wom/web/limits.py` holds all of this. `db.export_rows` yields in
batches and the response streams, so asking for the whole history (66,000 rows
here, 5 MB) neither builds a list in memory nor times out. A metric the account
is unranked on exports as blank rather than the API's `-1`, so an empty cell
means "not on the hiscores" and not "zero".

The charts are drawn in the browser with D3 (`wom/web/static/charts.js`). The
server only answers with JSON from `/api/chart/<key>`, so ticking a player or
changing a dropdown refetches a few kilobytes and redraws in place instead of
reloading the page. What each chart shows - the metric lists and the dropdown
choices - lives in `wom/catalog.py`, which is the half the server has to agree
with. D3 itself is vendored under `wom/web/static/`, so the dashboard needs no
CDN and works on a network with no internet at all.

Hovering a column slice gives the player, metric and amount; an axis icon gives
every player's number for that skill or boss, ranked; and a line chart shows
one crosshair that reads off all the players at once. Clicking a legend entry
hides that player without a refetch. All of it answers to touch as well - see
**Phones** below.

Because the server renders no pictures, there is nothing to cache and
invalidate: each request is one pass over the queries. The icon sprites, which
never change, are served with a week-long cache header - a 24-column chart asks
for 24 of them on every redraw.

### What is public and what is not

The pages people are given are read-only, and the API keys are never served.
Everything that writes is under `/admin`: the tracked player list, the keys,
the summary settings, the colours, the prompts, and buttons to run an update,
a round of summaries, or a history re-import. Those buttons run their work on a
background thread and report progress to a page that polls, rather than holding
a request open for a minute.

Responses carry a CSP that forbids inline script outright - the two pages that
had any load it from `/static` instead - plus `nosniff`, `DENY` framing,
`no-referrer` and, over HTTPS, HSTS. Anything server-supplied that reaches the
chart tooltips is escaped, since those are assembled as HTML, and text columns
in the CSV export are prefixed when they start with a character a spreadsheet
would run as a formula.

Admin exists only when `WOM_ADMIN_PASSWORD` is set - the routes are not
registered otherwise. The session cookie is `Secure`, `HttpOnly` and
`SameSite=Lax`; sign-in failures are counted per address and lock that address
out for five minutes after six of them. Set `WOM_SECRET_KEY` too, or sessions
end whenever the process restarts.

An admin job and the six-hourly schedule cannot overlap: both take the same
flag on the scheduler, so pressing **Update now** a minute before the slot
gets "the scheduled update is running" rather than a second pass over every
player - which for summaries would be a second set of paid API calls.

## Schedule

Updates run every six hours, at **12am, 6am, 12pm and 6pm US Eastern**, and
follow Eastern daylight saving rather than your own clock. The times are fixed
in `SLOT_HOURS` in `wom/scheduler.py` and are not configurable from the UI.

A slot that passed while the machine was off is caught up as soon as the app
starts, so a gap never silently swallows an update. The toolbar shows when the
last update ran and when the next slot is due, in your local time with the
Eastern time alongside. **Update now** runs a pass immediately without
disturbing the schedule.

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
`charts.js` reads the same variables back for its axes, gridlines and tooltips,
so a chart can never land on a background it does not match. There is no light
mode or toggle - changing the constants in that module changes everything.

The dashboard has four pages: **Summary** (charts comparing the players you
tick), **Milestones** (the achievements Wise Old Man has recorded),
**Summaries** (the written notes) and **Players** (the latest stored figures).
A fifth, **Admin**, appears when a password is configured.

### Player colours

Every player gets a colour from the default palette by their position in the
list, and that colour is used for them in every chart and every swatch. Pick a
different one on the admin page. Choices are stored in `player_colors` in
`data/config.json`, keyed by lowercase username.

### Summary period

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

### Summary charts

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

Updates land at least four times a day, and more whenever a plugin or a manual
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

The charts adapt too, in `charts.js` rather than CSS, because an SVG cannot
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

### Telling the summaries about the gaps

The written summaries face the same problem the charts do, and worse: a month
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
instructions, including telling the round-up not to rank a player on a total
that covers a longer stretch than everyone else's. The coverage line is part of
the digest, so it is part of the digest hash: adding it re-dated every stored
summary, which is what forced the last regeneration.

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
`Chart.prototype.trend` in `charts.js`.

## Milestones

A feed of every achievement Wise Old Man has recorded — 99s, base-stat
milestones, kill-count and XP thresholds — newest first, one row per milestone,
coloured by player and labelled with the same icons the charts use.

The **Since** dropdown filters to a rolling window or shows all time, and the
feed only lists the players included by the sidebar swatches, so it narrows the
same way the Summary tab does.

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

## What gets stored

`data/wom.db` (SQLite):

| Table | Holds |
| --- | --- |
| `players` | One row per tracked player: type, build, combat, XP, EHP, EHB, timestamps, and when their history was imported. |
| `snapshots` | Every distinct hiscore snapshot, kept whole as JSON. |
| `metrics` | The same snapshots flattened to one row per metric, for querying. |
| `achievements` | One row per milestone per player, with its date and how accurate that date is. |
| `summaries` | One written note per player per window. |
| `group_summaries` | One round-up per window, covering everyone. |
| `runs` | The history of update passes, with failures and their reasons. |

### Keeping the database small

Every snapshot costs about 30 KB once its JSON payload and ~113 metric rows are
counted, so six players at four updates a day is roughly **265 MB a year**.
Since charts only ever draw daily points, older readings beyond the first per
day earn nothing.

```bash
py wom_tracker.py --compact --dry-run
py wom_tracker.py --compact
```

thins history older than `--keep-days` (30 by default) to one snapshot per
player per day, then VACUUMs. On a database of 564 snapshots that removed 250 of
them and halved the file, 17.1 MB to 8.5 MB, with every charted value
unchanged — it keeps each day's *last* reading, which is exactly the point a
daily chart plots. Growth drops from ~265 MB a year to ~66 MB plus a rolling
30-day window of full detail.

It is deliberately manual and never runs on its own: deleting snapshots cannot
be undone, and the recent window has to stay raw for the day and week views to
mean anything.

Nothing is deleted unless you ask for it, so the metric history starts from the
imported back-catalogue and grows by four snapshots a day from there.

The schema is created and migrated on open, so an older `wom.db` is upgraded in
place rather than needing to be rebuilt.

## Updating

Each pass does `POST /players/{username}` per name, which asks Wise Old Man to
re-read that player from the hiscores. If the refresh is refused — the player
was updated moments ago, or is temporarily off the hiscores — it falls back to
`GET /players/{username}` so the stored profile still ends up current. Failures
are recorded per player and shown in the **Update history** table; one bad name
never stops the rest of the list.

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

A Summary chart is described once and drawn once. Describe it in
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

If it is a stacked or trend chart, that is all - `charts.js` already draws both
shapes, icons, legend, tooltips and the responsive behaviour. A genuinely new
shape needs a branch in `Chart.prototype.draw` and a drawing function beside
`stacked` and `trend`.

## Layout

```
web_app.py           the server: pages, API, and the schedule
wom_tracker.py       maintenance jobs (update, backfill, summarize, compact)
fetch_icons.py       downloads the skill and boss icons
Dockerfile           the container that runs on Fly
fly.toml             one always-on machine and a volume at /data
wom/
  api.py             Wise Old Man client, rate limiting and retries
  config.py          settings file, with secrets overridable by environment
  db.py              SQLite schema and queries
  updater.py         one update pass over the username list
  scheduler.py       the six-hourly Eastern timer and its busy flag
  periods.py         the day/week/month/quarter/year windows
  icons.py           skill order, and where each sprite lives
  catalog.py         what the Summary charts show
  colors.py          the palette and per-player colour overrides
  context.py         ViewContext: what a chart builder is handed
  summaries.py       the digest, the Claude call and the once-a-day hook
  logs.py            per-entry-point log files
  theme.py           the palette, emitted as CSS variables
  util.py            formatting helpers
  web/
    app.py           routes: the pages and /api/chart/<key>
    admin.py         the password-gated half
    data.py          the JSON behind each chart
    jobs.py          background jobs the admin buttons start
    static/          D3 and charts.js, the browser-side drawing
    templates/       the pages themselves
assets/skills/       skill icons (from RuneLite)
assets/bosses/       boss and activity icons (hiscore sprites, via Wise Old Man)
data/                config, database, prompts and logs (created on first run)
```

## Requirements

Python 3.10+ with `requests`, `flask` and `waitress`, plus `tzdata` (Windows
has no IANA time zone database of its own) and `anthropic` for the written
summaries:

```bash
py -m pip install -r requirements.txt
```

Nothing needs a display: the charts are drawn in the browser, so there is no
matplotlib and no image library on the server. Without `tzdata` the scheduler
falls back to a built-in US Eastern rule (`_UsEastern` in `wom/scheduler.py`),
which matches the real zone for any year using the post-2007 daylight saving
dates. Without `anthropic` everything works except the written summaries.
