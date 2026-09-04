# WOM Tracker

A web app that keeps a list of Old School RuneScape accounts updated from Wise
Old Man every ten minutes, charts what changed, runs a daily maxing
leaderboard, and writes short recaps of it with the Claude API. One process
serves the pages and runs the schedule; the charts are drawn in the browser
with D3.

Built against the [Wise Old Man v2 API](https://docs.wiseoldman.net/api). MIT
licensed - see [LICENSE](LICENSE). Longer background on why parts of it are
shaped the way they are is in [docs/notes.md](docs/notes.md).

One thing to know before running it: **recaps are Anthropic-only**. They are
opt-in and off by default, and everything else works without them - the charts,
tables, export and leaderboard are all computed from the stored readings.
Turning them on needs a Claude API key. Any OpenAI-compatible provider would fit
behind `generate()` in `wom/summaries.py`; nothing does yet.

## Running it

```bash
py -m pip install -r requirements.txt
py web_app.py --with-scheduler
```

That serves the dashboard on <http://localhost:8000> and runs the updates and
recaps from the same process. Without `--with-scheduler` it serves what is
already stored, which is what you want if something else is doing the updating.

The public pages are read-only. Everything that changes anything - the tracked
players, the API keys, the time zone, the colours, the prompts, and buttons to
run an update now - lives under `/admin`, behind a password:

```bash
WOM_ADMIN_PASSWORD=something-long py web_app.py --with-scheduler
```

Without that variable the admin routes are not registered at all, so a
deployment that forgets to set one has no admin pages rather than open ones.

### Hosted

`Dockerfile` and `fly.toml` deploy the same thing to [Fly.io](https://fly.io),
which is where it is meant to live: a machine that is always on, so the schedule
keeps running without a PC left awake. The volume mounts at `/data` and holds
the database, the settings and the prompts; keys arrive as secrets and never
touch the volume. `auto_stop_machines` is off on purpose - a stopped machine
cannot wake itself to run an update.

### Deploying a change

```bash
py deploy.py
```

`fly deploy` builds from the working directory rather than from git, so it will
happily ship code that was never committed and nothing afterwards can tell you
it did. `deploy.py` closes that: it refuses to deploy a dirty tree, runs the
tests, deploys, then pushes - so what is running, what is committed and what is
published stay the same thing. `--dry-run` checks without deploying.

### Maintenance jobs

```bash
py wom_tracker.py --update      # one update pass now
py wom_tracker.py --summarize   # write whatever recaps are owed
py wom_tracker.py --compact     # thin old history to one reading a day
py wom_tracker.py --list        # print the tracked usernames
py backup.py                    # pull a verified copy of the hosted database
```

The server does the first three on its own; these are the same jobs by hand.
Against a hosted deployment, run them where the database is:

```bash
fly ssh console -C "python /app/wom_tracker.py --compact"
```

## The pages

`/help` explains the whole thing to the people it is for - where the numbers
come from, how to set Dink up, and how the day and the month are won - in
plain language and without a word about how any of it is built. It is not in
the nav, because it is read once: a box under the sidebar links to it from
every page that has one.


Six public pages, with the player ticks and the period in a sidebar that drives
all of them, plus **Admin** behind the password. The one exception is the
leaderboard, which always judges every tracked account - see below.

- **Overview** - the standings and the charts: experience gained by skill, by
  player, and over time. Hovering reads off the figures; clicking a legend entry
  hides that player.
- **Maxing** - the leaderboard calendar, the day's standings, and experience
  toward 99 plotted midnight to midnight. Click an account for the skills
  behind its day.
- **Milestones** - 99s, level and boss-kill landmarks, newest first. A leading
  `~` means Wise Old Man only knows the date roughly.
- **Recaps** - the newest daily and monthly recap of the group, each carrying
  what the leaderboard decided, then a tree holding every earlier one and each
  account's own notes.
- **Players** - one row of headline figures each; click a row for that player's
  skills, bosses and activities, and what moved this period.
- **Data** - every metric as a sortable, filterable table, with export behind a
  button.

What the reader picks is remembered by their browser, so opening the site
tomorrow starts where they left it rather than back at every account over the
last week: the sidebar's ticks and window, the Milestones and Gallery kind
filters, and each Overview card's dropdown and mode. It is `localStorage`, so
it never leaves the machine and nothing about it reaches the server - see
[Selections, and where they are kept](docs/notes.md#selections-and-where-they-are-kept).
A link with a query string always wins over it, so a shared view still means
the same thing to whoever opens it.

## The leaderboards

Two competitions over the same days, on the same readings, disagreeing on
purpose. **Maxing** counts experience only up to ninety-nine in each skill and
a ninety-nine takes a day outright; **Grinding** counts all of it and nothing
else. An account with everything maxed cannot place on the first and can win
the second, which is the point of having both.

One rule, `winners.key(shown, board)`, and one page template. Grinding shows a
single Wins column where Maxing splits it: a ninety-nine never takes a grinding
day, so the split would be a column that could only ever read nothing.

## The Maxing Leaderboard

The **Maxing** tab colours a calendar by who took each day: whoever reached a 99
takes it, two beat one, and failing that it goes on experience counted only up
to level 99 in each skill - so an account with everything maxed cannot take a
day off people still climbing. A day is blank unless the tracker actually polled
everyone that day. A month goes to the best average across its days, and is not
awarded at all unless a fortnight of them counted. Mousing over the description
on the page gives the whole rule.

Below the calendar, in its own card, is the day still running - ranked by the
same measure and deliberately not a verdict, since today has not been polled to
its end. Below that again the same figures are drawn as a line per account,
midnight to midnight, so the axis is the whole day rather than however much of
it has happened. Opening an account gives the skills behind its day, including
the experience past 99 that the rule does not count. The prose lives on the
Recaps tab; this page is figures.

All three - the row, the skills behind it and the line under it - open the day
at the same reading, through `winners.day_span`. Wise Old Man stamps a reading
when the hiscores move, so an evening's last hour often arrives seconds into
the next day, and which reading opens the day decides whose day that work
counts toward.

The calendar and the standings under it **ignore the sidebar's ticks**, and
judge every tracked account whatever is ticked. It is one competition with one
answer: narrowed to three of six it silently becomes a different competition,
and the squares would recolour to a result nobody was playing for. The two are
given the same set as each other for the same reason - the standings tally each
account's wins this month from the very verdicts the squares are coloured by, so
a calendar judged across everyone beside a table judged across three would
credit different days on one page. The verdict chips on the Recaps tab quote the
calendar, so they are asked the same question too.

The chart below them does follow the ticks - it is a line per account, and
thinning it is what the ticks are for. Nothing on this page reloads when they
change; the chart simply redraws.

## Recaps

Written per **calendar window** rather than over a rolling period: "Saturday 29
August", "August 2026" - a closed span with a name, so a recap can be filed,
kept, and compared with the one before it. The first update of each local day
writes whatever has closed and not yet been written, so a machine asleep through
the 1st still writes that month when it wakes.

There are two kinds, and they cover different windows.

The **group recap** on the Recaps tab is the leaderboard's feed, so it covers
what the leaderboard judges: the **day** and the **month**, and nothing else.
Each one carries the leaderboard's own verdict beside it - who took that day,
who took that month, or that the month went unawarded. The recap decides on its
reading and the calendar decides on the rule; where the two differ, the squares
followed the calendar.

A **player's own notes** cover all five windows - day, week, month, quarter and
year - because those are about one account's progress, which a quarter still
says something about even where the leaderboard has no verdict for it. They sit
under that account's own branch in the tree below the group's, and carry no
verdict: a note about one account is not something the calendar has an opinion
about.

Each stores a hash of its digest, so a player whose numbers have not moved is
skipped rather than re-billed. A day costs well under a cent; a full set of
windows across six players is a few cents. The model and how hard it thinks
are both set under Admin, and both move that figure.

What each one is told is a prompt you can edit, under **Admin → Prompts**. Two
base prompts cover every window - one for a player's own note, one for the group
recap - and any window may override its own, which is how a yearly retrospective
can be asked for something a daily note should not say. The page offers a group
override only for the day and the month, since a group prompt for a quarter
would be a file nothing ever loads. It lists every prompt there is, creates an
override seeded from the base, and removes one to fall back again. They live in
`data/`, so they are yours and survive a redeploy; `backup.py` brings them down
with the database.

## Schedule

Updates run **every ten minutes**, on the wall-clock boundary. Milestones are
fetched on the hour rather than every pass - they move rarely and cost a request
per player. A slot that passed while the machine was off is caught up when it
starts.

Everything dated - day boundaries, the calendar, the window each recap covers
- follows midnight in the **time zone set under Admin**, so it tracks that
place's daylight saving rather than the server's. The ten-minute interval itself
is `SLOT_MINUTES` in `wom/scheduler.py`.

## What is public, and what is not

The pages people are given are read-only, and the API keys are never served.
Everything that writes is under `/admin`. Responses carry a CSP that forbids
inline script, plus `nosniff`, `DENY` framing, `no-referrer` and, over HTTPS,
HSTS. Chart tooltips escape anything server-supplied, and CSV columns starting
with a character a spreadsheet would run as a formula are prefixed.

The session cookie is `Secure`, `HttpOnly` and `SameSite=Lax`. There are no CSRF
tokens: every admin action is a form POST authenticated by that cookie, and
`SameSite=Lax` is what stops another site posting one on a signed-in viewer's
behalf. That is the whole defence, so it is set explicitly rather than left to
the browser's default.

Sign-in failures are counted per address and lock that address out for five
minutes after six of them; a correct password costs nothing, so signing in
often cannot lock you out of your own admin page. The data endpoints allow 600
calls per address per five minutes. Above *that* sits a tripwire on the total
across everyone, which does not slow anything down - it stops, and stays
stopped, writing the latch to the settings file so a restart does not resume
serving. It is deliberately far out of reach: a refused call is never counted,
so one machine can only ever contribute its own 600, and tripping it needs
dozens of addresses at once rather than a busy evening on a shared link.
Exports are five per address per six hours, and twenty a day across everyone.

One endpoint takes writes without a password: `/hook/dink/<token>`, where a
RuneLite plugin reports a login or a logout. See below. It sits outside the tripwire on
purpose - a login that is refused is gone for good, where everything the
tripwire protects can be fetched again - and is capped at thirty calls per
player per five minutes instead.

### Knowing who is calling

All of that depends on telling visitors apart. Behind a proxy `remote_addr` is
the proxy, which pools everyone into one bucket; but a proxy header is only
worth believing if something in front of you overwrites whatever the caller
sent. Trust one unconditionally and it becomes a dial the caller controls -
rotate the header and every request counts as a new person, which buys unlimited
password guesses. So no header is trusted unless you name it:

```bash
WOM_TRUSTED_IP_HEADER=Fly-Client-IP      # Fly.io, which overwrites it
WOM_TRUSTED_IP_HEADER=CF-Connecting-IP   # Cloudflare
WOM_TRUSTED_IP_HEADER=X-Forwarded-For    # nginx, Caddy - the weakest to trust
```

Leave it unset when nothing is in front of the app. `X-Forwarded-For` is a list
the client can prepend to, so its leftmost entry - the one read here - is
client-controlled unless your proxy rewrites the header rather than appending.

### Environment variables

Everything is optional except the admin password, without which there is no
admin.

| Variable | What it does |
| --- | --- |
| `WOM_ADMIN_PASSWORD` | Enables the admin pages. Unset, they are not registered at all. |
| `WOM_SECRET_KEY` | Signs the admin session cookie. Unset, a fresh key is minted per process and every restart signs you out. |
| `WOM_DATA_DIR` | Where the database, settings and prompts live. Defaults to `data/` beside the code. |
| `WOM_TRUSTED_IP_HEADER` | The proxy header carrying the real client address. See above. |
| `ANTHROPIC_API_KEY` | The recaps' key. Supplied here it cannot be read or changed from the admin page, which is the point. |
| `WOM_API_KEY` | Optional Wise Old Man key. Without one the API allows 20 requests a minute, which is ample: six players is twelve requests every ten minutes. |
| `WOM_INSECURE_COOKIE` | Drops `Secure` from the session cookie, for reaching admin over plain HTTP on a LAN. Not for anything public. |
| `TZ` | Only affects what the logs print. Day boundaries follow the time zone set under Admin. |

## Session logins

Wise Old Man can only ever tell us a session has *ended*: the hiscores do not
move until logout, so three hours of training arrives as one jump and lands in
whichever ten-minute window we happened to notice it in.

[Dink](https://github.com/pajlads/DinkPlugin), a Plugin Hub plugin, reports
both ends as they happen. Point its **Custom Metadata Handler** setting at a
URL and it POSTs a login - about six seconds in, not on world hops - carrying
that account's own live reading of its experience, and a logout, which carries
only the fact and the moment. The logout beats what we can infer twice over: it
is the moment itself rather than a ten-minute bracket, and it arrives however
little was gained, where the hiscore route says nothing under 10,000 experience.
Setting that one field is all a player has to do; the Discord webhook box beside
it can stay empty.

Each event is stored twice over in time: `received_at` is when the POST reached
us, `happened_at` is the moment Dink stamped it with. Attribution measures
between the second, because Dink retries a delivery it could not make and a
session resolved from arrival would be shortened by however long the retry
took. A client clock more than thirty minutes from ours is not believed - the
payload is the one part of that request nobody had to prove.

Issue and revoke a player's URL from the admin page, which also shows whether
each account is in game, which world, and when we last heard from them. That
status is inferred from the last event and is only ever as good as the last
thing Dink managed to send - a client that crashed sent no logout, so a login
older than a session could be stops claiming they are still playing. It must be the **https** one the page prints: waitress
strips `X-Forwarded-Proto`, so the app cannot tell how it was reached and the
URL is built as https for any non-local host rather than read off the request.
An http URL is redirected, okhttp turns the redirected POST into a GET, and the
body is lost - so the endpoint answers a GET by naming the scheme rather than
returning a bare 405. The plugin cannot send a header, so the URL *is* the
credential, and each player gets their own: it says who is calling without
trusting the name in the body, and one that leaks is revoked alone. Whatever
Dink offers about someone's Discord account or clan is dropped as the body is
read, never stored.

A player can opt into more. Putting the same URL in **Primary Webhook URLs**
as well - not instead, the metadata field is what carries the logins - and
ticking *Collection Log*, *Level Up*, *Kill Count*, *Quests*, *Achievement
Diary* and *Combat Achievements* sends what happens during a session too. Every
notifier ships off, so nothing else comes with them.

Collection log slots, quests, diaries, combat tasks and pets join Wise Old Man's
own milestones on the **Milestones** page, merged and sorted together, each row
tagged with what it is so the filter above the table can hide a kind. Levels and
boss counts stay off it: those are progress, and progress belongs on a chart.

Deaths and pets can also carry a screenshot, and those appear on the **Gallery**
page, ten of each with a toggle per kind. Images are accepted for those two
kinds and refused for every other, because a public endpoint that takes
arbitrary bytes should take as few as it can. The format is read out of the
bytes rather than believed from the request, the file is named by the digest of
its own contents so nothing a client sends becomes a path, and forty of each
kind are kept against a 250 MB ceiling. They live on the volume rather than in
the database - `backup.py` does not carry them, which is the deliberate answer
for something decorative.

Those land in `game_events` whole, because the interesting part is the detail
no metric has room for: which item, from which drop, at which rank. Where the
payload *is* a metric we already track, the value is also written at the moment
it happened - a collection log slot and a boss count both are - so those stop
being rounded to the next ten-minute reading. Levels are kept but not written
through: our level total shares a row with overall experience, and half a row
would read as a whole one.

Kill Count ships at every fiftieth kill; set *Kill Count Interval* to 1 for
every one. Experience itself cannot be streamed - Dink's XP milestones fire
only for skills already at 99, at a million or more apart.

Where both ends of a session are known and it crossed a local midnight, the app
records what the account had earned by that midnight, so a four-hour evening
session is credited to the evening rather than entirely to the minute after it
ended. Every total, chart and leaderboard picks that up without special-casing
it. An account with no session events is untouched and reports exactly what it
always did.

## What gets stored

SQLite, in `data/wom.db`. A reading repeats the one before it for most metrics -
a boss sitting at zero was being written again every ten minutes forever - so a
row is stored only when a value actually moves, and every read carries the last
one forward. Rank is not a move: a hiscore position drifts because strangers
played, and that alone was 83% of every row ever written.

That took six players' year of history from 17.9 MB to under 3 MB. Beyond 30
days, compaction thins what is left to one reading a day, which is finer than a
month-wide chart can draw; it runs once a day, on the first update after
midnight.

With two exceptions. Each reading records an `origin`: `poll` when our own
request caused Wise Old Man to take it, `archive` when it already existed and
we were only collecting it, `derived` when the app worked it out, `reported`
when a plugin told us outright. Compaction thins only `poll`, and recomputing
attribution clears only `derived` - a reported value is evidence, not
arithmetic. Compaction never thins an `archive` reading. A
polled one can be taken again tomorrow, so thinning it costs a detail; an
archive one is a moment recorded without us - a player's client pushing on
logout, most often - and is the only evidence of when that session ended, so
thinning it loses the timestamp for good. They are cheap to keep: on the live
database they are 317 of 2,470 readings and carry 18,749 of 20,340 metric rows,
which is 92% of the history for 13% of the readings.

`backup.py` pulls a copy of the hosted database taken with SQLite's backup API
*inside* the container - a consistent file, not a copy with recent writes
stranded in the write-ahead log - then opens and counts it before believing it.
It brings the prompts and settings down beside it, since those live only on the
volume, and keeps the newest fourteen of each. `backup_schedule.ps1` registers
it as a daily task on Windows.

## Tests

```bash
py -m pytest -q
```

Every test runs against a throwaway data directory, so none of them touch a real
database or make a network call. `deploy.py` runs them before it deploys.

## Layout

```
web_app.py           the server, and with --with-scheduler the schedule too
wom_tracker.py       the same jobs once, by hand
deploy.py            commit-checked deploy, then push
backup.py            pull a verified copy of the hosted database
backup_schedule.ps1  registers backup.py as a daily Windows task
fetch_icons.py       download the skill and boss sprites into assets/
wom/
  api.py             Wise Old Man client, rate limiting and retries
  config.py          settings file, with secrets overridable by environment
  db.py              SQLite schema and queries
  updater.py         one update pass over the username list
  scheduler.py       the ten-minute timer, the configured zone, the busy flag
  periods.py         the windows: all five for a note, day and month for a recap
  summaries.py       the digest, the Claude call and the once-a-day hook
  winners.py         the Maxing Leaderboard rule: who took each day and month
  catalog.py         what the Overview charts show
  context.py         one request's players, period and memoised gains
  colors.py          per-player colour overrides
  theme.py           the palette, emitted as CSS variables
  icons.py           skill order, and where each sprite lives
  logs.py            one log file per entry point
  runtime.py         the interpreter version the entry points insist on
  util.py            timestamp parsing and number formatting
  web/
    app.py           the application factory: config, hardening, blueprints
    pages.py         the HTML routes
    api.py           /api/chart and /api/player, and the guard in front
    data.py          builds what each chart draws, one function per chart
    exporting.py     the Data page at /export, its CSV and JSON
    admin.py         the password-gated half
    views.py         rows into view models, so routes stay thin
    today.py         the day in progress: standings, breakdown, trend
    selection.py     which players and colours a request is about
    limits.py        budgets, the tripwire, and the caller's address
    hooks.py         the Dink webhook: session logins, the one public write
    timespan.py      the window every page is answering over
    dates.py         parsing the sidebar's dates, and refusing bad ones
    jobs.py          admin jobs on their own thread, with progress
    static/          app.css, D3, chartkit.js and a file per page
tests/               pytest, against a throwaway data directory
docs/notes.md        longer background on why parts are shaped as they are
data/                settings, database, prompts and logs (created on first run)
```

Three names worth knowing, because the code and the page disagree: the
**Maxing Leaderboard** is `winners.py`, `today.py` and `winner_calendar` in the
code; the **Data** page is `/export`, `exporting.py` and `export.html`; and a
**recap** is a `summary` in the database and in `wom/summaries.py`, which is
what the tables and the column names still call it.

## Requirements

**Python 3.9 or newer**, with `requests`, `flask` and `waitress`, plus `tzdata`
(Windows has no IANA time zone database of its own) and `anthropic` for the
recaps.

The floor is 3.9 because of `zoneinfo`; nothing here needs anything newer, and
every file parses under 3.7. It cannot live in `requirements.txt` - pip is a
Python program, so the interpreter is already chosen by the time that file is
read - so the entry points check it themselves and say so, in `wom/runtime.py`.
Without that check the failure is quiet: `zoneinfo` is imported inside a
try/except so a missing time zone *database* degrades rather than crashes, and
on too old an interpreter that same path turns every zone but US Eastern into
UTC while the admin page blames the machine.
Nothing needs a display: the charts are drawn in the browser, so there is no
image library on the server.
