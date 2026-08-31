# WOM Tracker

A small desktop app that keeps a list of Wise Old Man players updated every six
hours and shows the results as charts and tables. It sits in the system tray
between updates, so refreshes happen whether or not the window is open.

Built against the [Wise Old Man v2 API](https://docs.wiseoldman.net/api).

## Running it

```bash
py wom_tracker.py
```

Double-clicking `run_wom.pyw` starts it with no console window. To have it
start when you log in:

```bash
powershell -ExecutionPolicy Bypass -File setup_autostart.ps1
```

(`-Remove` undoes it.)

Command-line extras:

```bash
py wom_tracker.py --update
```

runs one update pass with no window and exits — handy if you would rather drive
the schedule from Windows Task Scheduler than leave the app running.
`--list` prints the tracked usernames, and `--backfill [name...]` re-imports
stored history (see below — normally this happens on its own).

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

## Web dashboard

A read-only view of the same data, for sharing with other people:

```bash
py web_app.py                    # http://localhost:8000, this machine only
py web_app.py --host 0.0.0.0     # reachable from the rest of your network
```

It prints the URLs to hand out. Four pages — Summary (the same four charts),
Milestones, Summaries and Players — with the player and period filters in the
sidebar.

The charts are drawn in the browser with D3 (`wom/web/static/charts.js`). The
server only answers with JSON from `/api/chart/<key>`, so ticking a player or
changing a dropdown refetches a few kilobytes and redraws in place instead of
reloading the page. What each chart shows — the metric lists and the dropdown
choices — lives in `wom/catalog.py`, shared with the desktop tab, so the two
front ends cannot drift apart. D3 itself is vendored under `wom/web/static/`,
so the dashboard needs no CDN and works on a network with no internet at all.

Hovering does more than the desktop tooltip: a column slice gives the player,
metric and amount; an axis icon gives every player's number for that skill or
boss, ranked; and a line chart shows one crosshair that reads off all the
players at once. Clicking a legend entry hides that player without a refetch.

Read-only is a design constraint, not an omission: the desktop app owns the
config and is the only writer, so nothing here can change a setting and the
Wise Old Man API key is never served. By default the server does not run the
update schedule either — leave that to the desktop app or a scheduled
`--update`. Pass `--with-scheduler` when the server is the only thing running.

Because the server renders nothing, there is nothing to cache and invalidate:
each request is one pass over the same queries the desktop tab runs. The icon
sprites, which never change, are served with a week-long cache header - a
24-column chart asks for 24 of them on every redraw.

### Starting and stopping it

Open the **Sharing** tab in the desktop app and press Start. The dashboard runs
inside the app process - a waitress server on a background thread - so there is
no console window to keep alive and nothing to clean up: closing the app stops
the server with it. Tick "Start the dashboard when this app starts" and it
comes back on its own.

The scheduler already lives in the desktop app, so the dashboard started this
way never runs a second one. `py web_app.py` still exists for running the
dashboard on its own (a headless box, say), and `--with-scheduler` is for when
that is the only thing running - do not use it alongside the desktop app.

### Sharing it with people elsewhere

```bash
winget install --id Cloudflare.cloudflared
```

Then press **Open link** on the Sharing tab. The app runs `cloudflared` as a
child process, reads the `https://....trycloudflare.com` address out of its
output, and shows it with Copy and Open buttons. Closing the link, stopping the
dashboard, or quitting the app all shut the tunnel down - a tunnel pointing at
a stopped server is a dead link, so stopping the dashboard closes it too.

The connection is made outwards from this machine, so there is no firewall rule
to add, no router to touch, and no admin prompt. Worth knowing: the link is
unlisted but not password protected, so treat it as a secret; a new one is
minted every time it starts; and it only works while the app is running and the
machine is awake.

`serve_web.ps1 -Tunnel` still does the same thing from a console if you prefer.

### Sharing it on your own network instead

```bash
powershell -ExecutionPolicy Bypass -File serve_web.ps1 -Lan
```

This one does need the port opened, from an **Administrator** PowerShell, once:

```bash
New-NetFirewallRule -DisplayName 'WOM Tracker' -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private
```

Without elevation that command fails silently, which looks like the rule
worked. Check it with `Get-NetFirewallRule -DisplayName 'WOM Tracker'`.

Either way, there is no authentication built in. That is fine for a private
link among people you trust; do not put it somewhere public.

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

`Config.save()` does not write the object's whole in-memory snapshot. The app
holds one `Config` for a session, and a `--update` run alongside it has its
own; a blind write of either snapshot would silently revert whatever the other
had written since. Reverting `last_run` is the one that bites - the scheduler
would decide a run was overdue and fire a duplicate pass over every player. So
`__setitem__` records which keys were touched, and `save()` re-reads the file
under the lock and lays only those keys over it.

Logging is split for the same reason: `RotatingFileHandler` rotates by renaming
the open file, which Windows refuses while another process holds it. The window
writes `data/wom.log`, CLI runs write `wom-cli.log`, and the standalone server
writes `wom-web.log`, so no two processes ever share one.

## One copy at a time

The window hides to the tray instead of closing, which makes launching the app
again look like nothing happened - while quietly starting a second scheduler
against the same database. `wom/single_instance.py` stops that: the first copy
binds a loopback port and holds it, and a second launch finds it taken, asks
the running copy to show its window, and exits.

A socket rather than a lock file, because the operating system releases it when
the process dies - a stale lock file after a crash would leave the app refusing
to start with no obvious fix. The two sides exchange a short greeting first, so
an unrelated program sitting on that port is detected rather than mistaken for
this app: the lock is skipped with a warning and the app still starts.

## The window

Dark throughout, in both the desktop app and the web dashboard. The palette
lives in `wom/theme.py` and is the single source for all three surfaces: it
styles ttk for the Tk window, sets matplotlib's rcParams so every desktop chart
is drawn on the panel colour, and is emitted as CSS custom properties that the
D3 charts read back for their axes, gridlines and tooltips. There is no light
mode or toggle; changing the constants in that module changes everything at
once.

Five tabs share one sidebar of tracked players:

- **Summary** (the landing tab) — a period dropdown over a scrolling column of
  charts comparing the players you include. More charts get added below.
- **Milestones** — a feed of the achievements Wise Old Man has recorded.
- **Summaries** — optional written notes on each player, see below.
- **Charts** — single-player and whole-roster charts, one at a time.
- **Tables** — sortable tables of the latest snapshot.

The sidebar does double duty. The swatch on each row is that player's chart
colour: filled when they are included in the Summary tab, a grey outline when
they are not. Click it to toggle (**All** / **None** above the list, or the
space bar). Clicking the name selects the player the Charts and Tables tabs
draw. New players arrive included.

### Player colours

Every player gets a colour from the default palette by their position in the
list, and that colour is used for them in every chart. **Right-click a player**
to open a picker with a saturation/value gradient, a hue strip, hex and RGB
fields, and the palette as one-click swatches; **Reset to default** puts them
back on their palette slot. Choices are stored in `player_colors` in
`data/config.json`, keyed by username, so they survive restarts.

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
less. Where the gap is large enough to matter, both front ends say so rather
than let it pass unremarked - the web charts caption it ("Short history:
someone from 06 Aug 2026 (25d)") and the desktop tab appends it to the
line above the charts.

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

Both front ends now dash those stretches. The ends of a dashed segment are real
readings; the middle is interpolation. Anything closer together than 4% of the
window (floored at a day and a half, so short windows never dash) is drawn
solid as before. `_plot_with_gaps` in `wom/ui/summary.py` does it for the
desktop, and the same rule lives in `Chart.prototype.trend` in `charts.js`.

### Chart tooltips

Hovering anything meaningful on a chart names it. On the stacked columns a
slice gives the player, the skill or boss, and the amount; the axis icons give
the skill or boss name, which matters because the icons are the only label that
axis has; and a legend swatch gives the player, so a colour on its own is
always identifiable. The Charts tab does the same for its bars, and its XP-over-
time line names the individual snapshot nearest the cursor.

That is the desktop tab; the web dashboard does its own hit-testing in D3,
described under "Web dashboard" above.

Charts register what each artist means with `add_hover(ax, artist, text)` from
`wom/ui/tooltip.py`, and a `ChartTooltip` attached to the canvas does the
hit-testing. The tooltip is a borderless Tk window rather than a matplotlib
annotation, so following the cursor costs no figure redraws. Pass a callable
instead of a string to label the individual point under the pointer, the way
the line chart does.

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

## Options

The **Options** button opens the settings dialog:

- **Tracked usernames** — one RuneScape name per line, updated in that order.
  Blanks and duplicates are dropped when you save.
- **Keep running in the tray** — closing the window hides it instead of quitting.
- **Delete stored history for removed names** — one-shot cleanup on save.
- **API key** — optional. Anonymous callers get 20 requests a minute; a key
  raises that to 100. The client throttles itself to stay under whichever
  limit applies.
- **Contact** — appended to the `User-Agent` header, as the API docs ask for.

Settings live in `data/config.json`, alongside the `player_colors` overrides set
by right-clicking a player.

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

Panels redraw lazily: only the visible tab is drawn, the rest are marked stale
and catch up when their tab is selected, and nothing is drawn while the window
is hidden in the tray. Selecting a player only invalidates Charts and Tables;
the swatches only invalidate Summary and Milestones.

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

## Adding charts and tables

All three tabs are registries, so a new view is one function.

A Summary chart is stacked below the existing ones automatically — add it to
`wom/ui/summary.py`:

```python
@SUMMARY_CHARTS.add("boss_gains", "Boss kills gained", height=4.0,
                    description="Shown under the chart title.")
def boss_gains(ax, ctx):
    for index, player in enumerate(ctx.selected):
        gains = ctx.db.metric_gains(player["id"], ctx.period.start_iso(), "boss")
        ...
```

`ctx.selected` is the included players and `ctx.period` the chosen window;
`ctx.db.metric_gains(player_id, since, kind)` returns `{metric: gained}` for
skills, bosses or activities, and `ctx.db.metric_history(player_id, metric,
kind, since=...)` gives a time series with its baseline point. Pass
`options=[...]` on the chart's entry in `wom/catalog.py` to get a dropdown of
your own in the card header; the selection arrives as `ctx.choice`. `ctx.color_for(player)`
gives that player's colour, and `_stacked_by_metric(ax, ctx, kind, metrics,
ylabel, empty)` draws the whole stacked-column-with-icons shape, so a new
comparison chart is usually just picking which metrics to pass it.

A **Summary** chart takes two steps, because it is drawn twice. Describe it
once in `wom/catalog.py` (key, title, description, dropdown choices), then write
the matplotlib version in `wom/ui/summary.py` decorated with `@_chart("key")`,
which pulls that description back out of the catalog. The web dashboard needs
its data shape in `wom/web/data.py` and its drawing in `charts.js`; if it is a
stacked or trend chart, both already exist and it is a few lines in `_BUILDERS`.

A chart draws onto a matplotlib `Axes` — add it to `wom/ui/charts.py`:

```python
@CHARTS.add("my_chart", "Title in the picker", needs_player=True,
            description="Shown next to the picker.")
def my_chart(ax, ctx):
    rows = ctx.db.metric_history(ctx.player_id, "slayer", "skill")
    if not rows:
        raise NoData("Nothing stored yet.")
    ax.plot([r["captured_at"] for r in rows], [r["value"] for r in rows])
```

A table returns columns and formatted rows — add it to `wom/ui/tables.py`:

```python
@TABLES.add("my_table", "Title in the picker")
def my_table(ctx):
    columns = [("Player", 160, tk.W, TXT), ("EHP", 80, tk.E, NUM)]
    rows = [(p["display_name"], fmt_float(p["ehp"])) for p in ctx.players]
    return columns, rows
```

`ctx` is a `ViewContext` (`wom/ui/base.py`) carrying `db`, `config`, the
highlighted `player`, the full `players` list, the included `selected` players and
the Summary `period`. A context is built fresh for every redraw, so
`ctx.gains(player, kind)` memoises freely - use it rather than
`db.metric_gains` so sibling charts share one pair of snapshot lookups. Raising `NoData` from a chart shows the message in place
of the plot instead of an error. Columns marked `NUM` sort numerically when the
header is clicked.

Useful `Database` helpers for new views: `metric_history(player_id, metric,
kind)`, `latest_snapshot_metrics(player_id, kind)`, `recent_runs(limit)`, and
`query(sql, params)` for anything else.

## Layout

```
wom_tracker.py       entry point and CLI
run_wom.pyw          console-free launcher
fetch_icons.py         downloads the skill and boss icons
setup_autostart.ps1  optional login shortcut
web_app.py           read-only web dashboard
serve_web.ps1        starts it, optionally shared over a link or your LAN
wom/
  api.py             Wise Old Man client, rate limiting and retries
  config.py          settings file
  db.py              SQLite schema and queries
  updater.py         one update pass over the username list
  scheduler.py       the six-hourly Eastern timer
  periods.py         the day/week/month/quarter/year windows
  icons.py           skill order and icon loading
  catalog.py         what the Summary charts show, shared by both front ends
  sharing.py         the dashboard and tunnel lifecycles, driven from the app
  single_instance.py the loopback lock that keeps one copy running
  colors.py          the palette and per-player colour overrides
  summaries.py       the digest, the Claude call and the once-a-day hook
  util.py            formatting helpers
  web/               Flask dashboard
    app.py           routes: the pages and /api/chart/<key>
    data.py          the JSON behind each chart
    static/          D3 and charts.js, the browser-side drawing
    templates/       the pages themselves
  ui/
    app.py           main window and player sidebar
    options.py       Options dialog
    summary.py       Summary tab: period picker and stacked charts
    summaries.py     Summaries tab: the written notes
    milestones.py    Milestones tab: the achievement feed
    sharing.py       Sharing tab: start/stop the dashboard and its link
    colorpicker.py   the gradient/hex/RGB colour dialog
    charts.py        chart registry and matplotlib panel
    tables.py        table registry and Treeview panel
    base.py          ViewContext and the registry type
    tooltip.py       hover labels for chart artists
    tray.py          system tray icon
assets/skills/       skill icons (from RuneLite)
assets/bosses/       boss and activity icons (hiscore sprites, via Wise Old Man)
data/                config, database and log (created on first run)
```

## Requirements

The web dashboard adds `flask` and `waitress`, and the written summaries add
`anthropic`; the desktop app runs without any of them.

Python 3.10+ with `requests` and `matplotlib`, plus `tzdata` (Windows has no
IANA time zone database of its own) and — for the tray icon — `pystray` and
`Pillow`:

```bash
py -m pip install -r requirements.txt
```

Both extras degrade gracefully. Without `pystray`/`Pillow`, closing the window
quits instead of hiding to the tray. Without `tzdata`, the scheduler falls back
to a built-in US Eastern rule (`_UsEastern` in `wom/scheduler.py`), which
matches the real zone for any year using the post-2007 daylight saving dates.
