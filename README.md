# WOM Tracker

A web app that keeps a list of Old School RuneScape accounts updated from Wise
Old Man every ten minutes, charts what changed, and writes short round-ups of
each day, week, month, quarter and year with the Claude API. One process serves
the pages and runs the schedule; the charts are drawn in the browser with D3.

Built against the [Wise Old Man v2 API](https://docs.wiseoldman.net/api). MIT
licensed - see [LICENSE](LICENSE). Longer background on why parts of it are
shaped the way they are is in [docs/notes.md](docs/notes.md).

One thing to know before running it: **round-ups are Anthropic-only**. They are
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
round-ups from the same process. Without `--with-scheduler` it serves what is
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
py wom_tracker.py --summarize   # write whatever round-ups are owed
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

Five public pages, with the player ticks and the period in a sidebar that drives
all of them, plus **Admin** behind the password.

- **Overview** - the standings and the charts: experience gained by skill, by
  player, and over time. Hovering reads off the figures; clicking a legend entry
  hides that player.
- **Milestones** - 99s, level and boss-kill landmarks, newest first. A leading
  `~` means Wise Old Man only knows the date roughly.
- **Round-ups** - the Maxing Leaderboard calendar and the written notes, each
  filed under the window it covers.
- **Players** - one row of headline figures each; click a row for that player's
  skills, bosses and activities, and what moved this period.
- **Data** - every metric as a sortable, filterable table, with export behind a
  button.

## Round-ups

Written per **calendar window** rather than over a rolling period: "Saturday 29
August", "24-30 August", "August 2026" - a closed span with a name, so a note
can be filed, kept, and compared with the one before it. The first update of
each local day writes whatever has closed and not yet been written: the day
itself, the week once a Monday has passed, the month once the 1st has. A machine
asleep through a Monday still writes that week when it wakes.

Each stores a hash of its digest, so a player whose numbers have not moved is
skipped rather than re-billed. A day costs well under a cent; a full set of
windows across six players is a few cents.

The **Maxing Leaderboard** on that page colours a calendar by who took each day:
whoever reached a 99 takes it, two beat one, and failing that it goes on
experience counted only up to level 99 in each skill - so an account with
everything maxed cannot take a day off people still climbing. A day is blank
unless the tracker actually polled everyone that day. A month goes to the best
average across its days, and is not awarded at all unless a fortnight of them
counted. Mousing over the description on the page gives the whole rule.

## Schedule

Updates run **every ten minutes**, on the wall-clock boundary. Milestones are
fetched on the hour rather than every pass - they move rarely and cost a request
per player. A slot that passed while the machine was off is caught up when it
starts.

Everything dated - day boundaries, the calendar, the window each round-up covers
- follows midnight in the **time zone set under Admin**, so it tracks that
place's daylight saving rather than the server's. The ten-minute interval itself
is `SLOT_MINUTES` in `wom/scheduler.py`.

## What is public, and what is not

The pages people are given are read-only, and the API keys are never served.
Everything that writes is under `/admin`. Responses carry a CSP that forbids
inline script, plus `nosniff`, `DENY` framing, `no-referrer` and, over HTTPS,
HSTS. Chart tooltips escape anything server-supplied, and CSV columns starting
with a character a spreadsheet would run as a formula are prefixed.

The session cookie is `Secure`, `HttpOnly` and `SameSite=Lax`. Sign-in failures
are counted per address and lock that address out for five minutes after six of
them. The data endpoints allow 600 calls per address per five minutes; a
tripwire above that latches - writing the latch to the settings file, so a
restart does not resume serving - until an admin clears it. Exports are five per
address per six hours, and twenty a day across everyone.

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
| `ANTHROPIC_API_KEY` | The round-ups' key. Supplied here it cannot be read or changed from the admin page, which is the point. |
| `WOM_API_KEY` | Optional Wise Old Man key. Without one the API allows 20 requests a minute, which is ample: six players is twelve requests every ten minutes. |
| `WOM_INSECURE_COOKIE` | Drops `Secure` from the session cookie, for reaching admin over plain HTTP on a LAN. Not for anything public. |
| `TZ` | Only affects what the logs print. Day boundaries follow the time zone set under Admin. |

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
wom/
  api.py             Wise Old Man client, rate limiting and retries
  config.py          settings file, with secrets overridable by environment
  db.py              SQLite schema and queries
  updater.py         one update pass over the username list
  scheduler.py       the ten-minute timer, the configured zone, the busy flag
  periods.py         the day/week/month/quarter/year windows
  summaries.py       the digest, the Claude call and the once-a-day hook
  winners.py         who took each day and month, by the group's rule
  catalog.py         what the Overview charts show
  colors.py          the palette and per-player colour overrides
  theme.py           the palette, emitted as CSS variables
  icons.py           skill order, and where each sprite lives
  web/
    app.py           the application factory: config, hardening, blueprints
    pages.py         the HTML routes
    api.py           /api/chart and /api/player, and the guard in front
    exporting.py     the export page, its CSV and JSON, and the date parsing
    admin.py         the password-gated half
    views.py         rows into view models, so routes stay thin
    limits.py        budgets, the tripwire, and the caller's address
    timespan.py      the window every page is answering over
    static/          app.css, D3, chartkit.js and a file per page
tests/               pytest, against a throwaway data directory
data/                settings, database, prompts and logs (created on first run)
```

## Requirements

Python 3.10+ with `requests`, `flask` and `waitress`, plus `tzdata` (Windows has
no IANA time zone database of its own) and `anthropic` for the round-ups.
Nothing needs a display: the charts are drawn in the browser, so there is no
image library on the server.
