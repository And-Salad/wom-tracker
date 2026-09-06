# WOM Tracker

[![tests](https://github.com/And-Salad/wom-tracker/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/And-Salad/wom-tracker/actions/workflows/tests.yml)

A web app that keeps a list of Old School RuneScape accounts updated from Wise
Old Man every ten minutes, charts what changed, runs a daily maxing
leaderboard, and writes short recaps of it with the Claude API. One process
serves the pages and runs the schedule; the charts are drawn in the browser
with D3.

Built against the [Wise Old Man v2 API](https://docs.wiseoldman.net/api). MIT
licensed - see [LICENSE](LICENSE). This file is how to run it; why it is
shaped the way it is lives in [docs/](docs), listed at the bottom.

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
py wom_tracker.py --summarize --dry-run   # price them and print every digest
py wom_tracker.py --compact     # thin old history to one reading a day
py wom_tracker.py --list        # print the tracked usernames
py backup.py                    # pull a verified copy of the hosted database
```

The server does the first three on its own; these are the same jobs by hand.
Against a hosted deployment, run them where the database is:

```bash
fly ssh console -C "python /app/wom_tracker.py --compact"
```

## Environment variables


Everything is optional except the admin password, without which there is no
admin.

| Variable | What it does |
| --- | --- |
| `WOM_ADMIN_PASSWORD` | Enables the admin pages. Unset, they are not registered at all. |
| `WOM_SECRET_KEY` | Signs the admin session cookie. Unset, a fresh key is minted per process and every restart signs you out. |
| `WOM_DATA_DIR` | Where the database, settings and prompts live. Defaults to `data/` beside the code. |
| `WOM_TRUSTED_IP_HEADER` | The proxy header carrying the real client address. See [Knowing who is calling](docs/security.md#knowing-who-is-calling). |
| `ANTHROPIC_API_KEY` | The recaps' key. Supplied here it cannot be read or changed from the admin page, which is the point. |
| `WOM_API_KEY` | Optional Wise Old Man key. Without one the API allows 20 requests a minute, which is ample: six players is twelve requests every ten minutes. |
| `WOM_INSECURE_COOKIE` | Drops `Secure` from the session cookie, for reaching admin over plain HTTP on a LAN. Not for anything public. |
| `TZ` | Only affects what the logs print. Day boundaries follow the time zone set under Admin. |

## The pages

`/help` explains the whole thing to the people it is for - where the numbers
come from, how to set Dink up, and how the day and the month are won - in
plain language and without a word about how any of it is built. It is not in
the nav, because it is read once: a box under the sidebar links to it from
every page that has one.


Seven public pages, with the player ticks and the period in a sidebar that
drives all of them, plus **Admin** behind the password. The one exception is
the leaderboards, which always judge every tracked account - see
[docs/leaderboards.md](docs/leaderboards.md).

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

## Requirements

**Python 3.12 or newer**, with `requests`, `flask` and `waitress`, plus
`tzdata` (no slim Linux image carries an IANA time zone database, and neither
does Windows) and `anthropic` for the recaps.

3.12 because it is the oldest release still receiving security fixes. The
packages themselves ask for less - `anthropic` and `requests` want 3.10,
`flask` and `waitress` 3.9 - so this is a support decision rather than a
technical floor: 3.10 stopped getting fixes on 31 October 2026. The Docker
image ships 3.14, which is newer on purpose: a floor is the oldest thing a
clone should need, and the container is ours. The tests run on both. It
cannot live in `requirements.txt` - pip is a Python program, so the
interpreter is already chosen by the time that file is read - so the entry
points check it themselves and say so, in `wom/runtime.py`.
Without that check the failure is quiet: `zoneinfo` is imported inside a
try/except so a missing time zone *database* degrades rather than crashes, and
on too old an interpreter that same path turns every zone but US Eastern into
UTC while the admin page blames the machine.
Nothing needs a display: the charts are drawn in the browser, so there is no
image library on the server.

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
`.github/workflows/tests.yml`. The suite on 3.12 and 3.14 - the floor and
the shipped runtime - the browser tests,
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

Build it on the version the Dockerfile ships, not on the floor. The point of
a local environment is to behave like the one the code will actually run in,
and the floor is covered by CI on every push. To check the floor before
pushing anyway - worth doing if a change reaches for anything recent - put a
second environment on it:

```bash
py -3.12 -m venv .venv-floor
.venv-floor/Scripts/pip install -r requirements-dev.txt
.venv-floor/Scripts/pytest -q
```

`.venv*` is ignored, so any number of these can sit side by side.

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
docs/                why it is shaped as it is; see The rest of it, below
data/                settings, database, prompts and logs (created on first run)
```

Three names worth knowing, because the code and the page disagree: the
**Maxing Leaderboard** is `winners.py`, `today.py` and `winner_calendar` in the
code; the **Data** page is `/export`, `exporting.py` and `export.html`; and a
**recap** is a `summary` in the database and in `wom/summaries.py`, which is
what the tables and the column names still call it.

## The rest of it

The README is how to run the thing. Why it is shaped the way it is lives
next door:

- [docs/leaderboards.md](docs/leaderboards.md) - the two competitions, how a
  day and a month are decided, and what the written round-ups settle.
- [docs/data.md](docs/data.md) - the schedule, what a player's own client
  reports, and what is kept on disk.
- [docs/security.md](docs/security.md) - what is public, what is behind the
  password, and how callers are told apart.
- [docs/notes.md](docs/notes.md) - the longer background: the palette, the
  charts, the traps found on the way.
