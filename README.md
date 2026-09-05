# WOM Tracker

[![tests](https://github.com/And-Salad/wom-tracker/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/And-Salad/wom-tracker/actions/workflows/tests.yml)

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

Push, then press the button: **Actions -> deploy -> Run workflow**. It runs the
full suite, the browser tests and the linter against that commit, deploys only
if all of them pass, and then waits for the site to answer before it calls the
run green.

`fly deploy` builds from the working directory rather than from git, so run
from a laptop it will happily ship code that was never committed and nothing
afterwards can tell you it did. Deploying from Actions removes that rather than
guarding against it: the runner checks out one commit into an empty machine, so
what ships is what is pushed, by construction.

It is a button rather than something that fires on every merge, because this is
one always-on machine with the update schedule inside it - a deploy restarts the
tracker and interrupts whatever pass was mid-flight. The schedule catches the
slot up afterwards, but the moment is worth choosing.

The run confirms this for itself. "Deployed" is Fly's word for the machines
having been told, so the workflow tags the image with the commit and then asks
the machines what they are actually running - every one of them, since a fleet
where only some had moved is the state worth failing on. Only then does it
check the site answers.

`/admin` says the same thing for a person: which commit is answering, and how
long that process has been up. The commit says what code is running and the
uptime says whether this is a process that started after the deploy or the one
that was already there. It is baked into the image at build time; running from
a clone it comes from git instead, and if neither can say, the page says so
rather than showing a blank.

It needs one secret, set once:

```bash
fly tokens create deploy -x 999999h        # prints a token
gh secret set FLY_API_TOKEN                # paste it at the prompt
```

`deploy.py` still works and is still the way to deploy when GitHub is not
answering. It refuses a dirty tree, runs the tests, deploys, then pushes;
`--dry-run` checks without deploying. It builds from the working directory,
which the workflow does not, so prefer the button when there is a choice. The
two flags that weaken it have to be typed: `--skip-tests`, and `--off-main`
for deploying a branch, which leaves the site on a commit `main` does not
have.

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


Seven public pages, with the player ticks and the period in a sidebar that
drives all of them, plus **Admin** behind the password. The one exception is
the leaderboards, which always judge every tracked account - see below.

- **Overview** - the standings and the charts: experience gained by skill, by
  player, and over time. Hovering reads off the figures; clicking a legend entry
  hides that player.
- **Leaderboards** - Maxing and Grinding, one on screen at a time: the winner
  calendar, the day's standings, and the day plotted midnight to midnight.
  Click an account for the skills behind its day.
- **Milestones** - 99s, level and boss-kill landmarks, newest first. A leading
  `~` means Wise Old Man only knows the date roughly.
- **Recaps** - the newest daily and monthly recap of the group, each carrying
  what the leaderboard decided, then a tree holding every earlier one and each
  account's own notes.
- **Gallery** - the screenshots players' own clients sent with a death or a
  pet, each kind on its own toggle. Click one to open it.
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

One rule, `winners.key(shown, board)`, and one page. Grinding shows a single
Wins column where Maxing splits it: a ninety-nine never takes a grinding day, so
the split would be a column that could only ever read nothing.

They share the **Leaderboards** tab and a toggle, the same one the recaps use.
Both are rendered whichever is chosen and the other is put away - one more pass
over the same two months - so switching costs nothing. `?board=` picks the one
shown, which is what the toggle remembers between visits and what lets
`/maxing` and `/grinding` still answer, as redirects to the board they named.

## The leaderboard page

It colours a calendar by who took each day. On **Maxing**: whoever reached a 99
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

Round-ups are written per calendar window and now per leaderboard: the day, the
week and the month, for Maxing and for Grinding, which is six a cycle. A window
is owed until *both* boards have one - settling it on the first would leave the
second with nothing for ever, because the window never comes due again.

The week is the new one and it is not a verdict. Neither leaderboard awards a
week, so its round-up reviews instead: who took each of its days, and where
that leaves the month's running average. It carries no winner chip, because
there is no award to name.

Each board's digest opens with its own rule. The two are the same figures
judged differently, and a round-up handed only the numbers would pick the
winner the numbers suggest rather than the one who won.

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

## Readings we did not take

`update_player` refreshes a player and hands back their latest snapshot and
nothing else. Wise Old Man keeps one whenever *anybody* asks it to look - most
often a player's own client pushing at logout - and one of those made between
two of our polls is invisible to us however often we ask.

That matters at a day boundary. Somebody who logs out at 23:55 is noticed by
our poll at 00:10, and their whole evening lands on the wrong day. So each pass
also re-reads history and keeps anything new, which puts that reading back
where it happened - for every account, with nothing for anyone to install.

It reads from just before its own last reading rather than a flat window, so
normally that is a snapshot or two. Asked for a flat three hours it returned
every ten-minute reading in them, almost all already held, and that alone
turned an eighteen second pass into a minute. After an outage the window
widens by itself, because the last reading is older.

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
pip install -r requirements-dev.txt
pytest
```

Every test runs against a throwaway data directory of its own - its database,
its settings and its prompts - so none of them touch a real installation or
make a network call, and none of them can leave anything behind for the next
one. They run in a different order every time, which is what keeps that true.
The deploy workflow runs them before it deploys, and so does `deploy.py`.

The browser half has its own, because it needs a different runtime:

```bash
npm ci
npm test
```

`npm ci` rather than `npm install`: the lockfile is committed, so what the
tests run against is the same set of packages CI resolves and not whatever
the version range happens to mean today.

Node 22 or newer, which `engines` in `package.json` states and the
`engine-strict` in `.npmrc` enforces at install time rather than leaving to
be discovered as a stack trace inside a dependency. CI runs 24.

Four jobs run on every push and pull request - see
`.github/workflows/tests.yml`. The suite on 3.12 and 3.14, the browser tests,
`ruff check .` as the linter (configured in `pyproject.toml`), and a build of
the Dockerfile, so an image that cannot be produced fails on the pull request
rather than in the middle of a deploy. All four are required to merge, and the
deploy workflow calls that same file rather than keeping a second copy of it -
so the checks that guard a pull request are exactly the ones that gate a
release.

### Working on it

A virtualenv, so this project's packages are not shared with every other one
on the machine:

```bash
py -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt    # Scripts/ on Windows, bin/ elsewhere
```

There is no need to activate it. `.venv/Scripts/pytest` and
`.venv/Scripts/ruff` run the versions inside it whether or not the shell has
been told about them - which is worth knowing on Windows, where activating
means `Activate.ps1`, and running it means a PowerShell execution policy that
a default install does not have.

Ruff can also run on the way into a commit, on the files being committed:

```bash
git config core.hooksPath .githooks
```

Once per clone. Git will not enable a hook a repository ships until it is
asked to - a clone that ran committed code before anybody read it would be a
way to hand somebody a shell - so this is opt-in by design rather than an
oversight. What it buys is the second it takes to find a lint error here
instead of the thirty it takes to find it in CI, and `main` is protected, so
that error does block a merge. It lints only staged Python, never the test
suite - a commit should stay cheap enough to make often - and
`git commit --no-verify` skips it.

## Layout

```
web_app.py           the server, and with --with-scheduler the schedule too
wom_tracker.py       the same jobs once, by hand
deploy.py            commit-checked deploy by hand, then push
backup.py            pull a verified copy of the hosted database
backup_schedule.ps1  registers backup.py as a daily Windows task
fetch_icons.py       download the skill and boss sprites into assets/
pyproject.toml       ruff and pytest configuration; no packaging metadata
requirements.txt     what the app needs
requirements-dev.txt what the tests need on top of it
package.json         jsdom, for the browser tests
wom/
  api.py             Wise Old Man client, rate limiting and retries
  config.py          settings file, with secrets overridable by environment
  build.py           which commit is running, for confirming a deploy
  db.py              the name everything imports; wom/store is the code
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
  store/
    schema.py        the tables as they are today
    migrations.py    getting an older file to them, numbered in user_version
    core.py          the connection, and the two ways of asking
    players.py       the roster, and what Wise Old Man says about each
    snapshots.py     readings, and the sparse metric history under them
    events.py        what players report while playing: sessions, milestones
    images.py        the gallery's rows
    achievements.py  milestones Wise Old Man dates for us
    recaps.py        the written notes and round-ups, and the runs behind them
    maintenance.py   thinning old history
tests/               a file per concern, each against its own data directory
  js/                the browser half, run by node against jsdom
.githooks/           ruff before a commit, opt-in per clone (see Tests)
.github/workflows/   the tests, on 3.12 and 3.14, the linter, and the deploy
.github/             CODEOWNERS, dependabot and how to report a security bug
docs/notes.md        longer background on why parts are shaped as they are
data/                settings, database, prompts and logs (created on first run)
```

Three names worth knowing, because the code and the page disagree: the
**Maxing Leaderboard** is `winners.py`, `today.py` and `winner_calendar` in the
code; the **Data** page is `/export`, `exporting.py` and `export.html`; and a
**recap** is a `summary` in the database and in `wom/summaries.py`, which is
what the tables and the column names still call it.

## Requirements

**Python 3.12 or newer**, with `requests`, `flask` and `waitress`, plus
`tzdata` (no slim Linux image carries an IANA time zone database, and neither
does Windows) and `anthropic` for the recaps.

3.12 because that is what the Docker image ships and what the tests run on.
The packages themselves ask for less - `anthropic` and `requests` want 3.10,
`flask` and `waitress` 3.9 - so this is a support decision rather than a
technical floor: 3.10 stopped getting security fixes on 31 October 2026. It
cannot live in `requirements.txt` - pip is a Python program, so the
interpreter is already chosen by the time that file is read - so the entry
points check it themselves and say so, in `wom/runtime.py`.
Without that check the failure is quiet: `zoneinfo` is imported inside a
try/except so a missing time zone *database* degrades rather than crashes, and
on too old an interpreter that same path turns every zone but US Eastern into
UTC while the admin page blames the machine.
Nothing needs a display: the charts are drawn in the browser, so there is no
image library on the server.
