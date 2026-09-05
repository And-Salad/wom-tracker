# CLAUDE.md

A web app that keeps Old School RuneScape accounts updated from Wise Old Man,
charts what changed, and writes recaps with the Claude API. One Flask process
serves the pages and runs the schedule; the charts are drawn in the browser
with D3.

This file is the short version, and exists because the long version does not
get read. [README.md](README.md) is thorough and 35KB, so it is not in context
by default; [docs/notes.md](docs/notes.md) is the background on why things are
shaped the way they are. What follows is only the handful of things worth
knowing *before* touching anything, plus the traps that cost a session to
rediscover.

## Running things

Everything goes through the virtualenv, which needs no activating:

```bash
.venv/Scripts/python.exe -m pytest
```

```bash
.venv/Scripts/python.exe -m ruff check .
```

`Scripts/` is the Windows layout; a clone elsewhere uses `.venv/bin/`. Do not
reach for a bare `python` on Windows - it is the Microsoft Store stub and fails
with a message about App Execution Aliases. `py` is the real launcher there,
but it is the system interpreter and has none of this project's packages, so it
answers `--version` and then fails on the first import.

There are two environments on purpose - `.venv` is the shipped runtime (3.14)
and `.venv-floor` is the interpreter floor (3.12) that `wom/runtime.py`
enforces. Build against `.venv`; reach for the floor only when a change uses
something recent. CI runs both.

The browser tests are `npm test` (Node 22+, from `tests/js/`). Check that node
is actually on PATH before trying - a populated `node_modules/` is not evidence
that it is, since the install may have happened on another machine or under a
version manager that is not loaded. If node is missing, leave those tests to
CI rather than spending the session installing one.

The dev server is `py web_app.py`, or the `wom` entry in `.claude/launch.json`
via the preview tools. Never run it with Bash.

Four CI jobs gate a merge: the suite on 3.12 and 3.14, the browser tests,
`ruff check .`, and a Dockerfile build. All four are required, and `main` is
protected. `.githooks/pre-commit` runs ruff on staged Python if you enable it
with `git config core.hooksPath .githooks`.

`.claude/settings.json` is committed and allows those checks to run without a
prompt each time. It is deliberately read-only: nothing in it installs,
deploys, commits or pushes, and `data/` is denied outright because that is
where the API keys live. Widen it only for commands with the same property.

## Conventions that are decisions, not accidents

**`str.format()`, not f-strings.** Used throughout, roughly 170 call sites, and
ruff's UP032 is switched off in `pyproject.toml` to keep it that way. Rewriting
them is a style opinion imposed by a tool, not a fix.

**Comments carry the reasoning.** Most non-obvious code here is preceded by a
comment saying what went wrong the other way. Match that when adding code, and
when changing code read the comment first - it is usually the argument against
the change you were about to make.

**No packaging metadata.** `pyproject.toml` is tool config only; the app is
cloned and run, not installed. Dependencies live in `requirements.txt` and
`requirements-dev.txt`, and nowhere else.

## Traps for automated analysis

Two patterns here defeat grep, and a dead-code pass that trusts grep will
delete working features:

**The `@chart(...)` registry.** Every builder in `wom/web/data.py` is
registered by key through the decorator in `wom/catalog.py` and never called by
name. `_standings`, `_xp_trend`, `_group_totals` and the rest look unreferenced
and are not.

**Runtime-generated CSS class names.** `admin.html` builds a class from a label
with `{{ player.state.label | replace(' ', '-') }}`, so `.logged-out`,
`.logged-in` and `.in-game` in `app.css` appear nowhere in the source. They are
live. The same goes for Flask route handlers, error handlers, `after_request`
hooks and autouse pytest fixtures - all referenced only by decorator.

## Layout

`web_app.py` is the server, `wom_tracker.py` the same jobs by hand, `deploy.py`
a commit-checked deploy. The package is `wom/`: the schedule and data side at
the top level, the Flask half under `wom/web/`, and storage under `wom/store/`
behind the `wom/db.py` name everything imports. The full annotated tree is
under "Layout" in the README.
