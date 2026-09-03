"""What this needs from the interpreter running it.

Nothing in requirements.txt can say "you need Python 3.x": pip is a Python
program, so by the time it reads that file the interpreter has already been
chosen. `python_requires` in package metadata is the mechanism that enforces
it, and this is an application you clone rather than a package you install.
So the entry points ask here, on the way in.

The floor is zoneinfo, which arrived in 3.9. Nothing else in the codebase
needs anything newer - every file parses under 3.7 - and the README used to
claim 3.10 for no reason anybody could point at.

It matters because the failure without it is quiet rather than loud. zoneinfo
is imported inside a try/except so a missing time zone database degrades
instead of crashing, which is right when the database is missing and wrong
when the interpreter is simply too old: US Eastern keeps working off the
built-in rules, every other zone silently becomes UTC - moving every day
boundary, every calendar square and every recap window - and the admin page
refuses each zone you type with "not a time zone this machine knows", blaming
the machine for the interpreter's age.
"""

import sys

MINIMUM = (3, 9)

MESSAGE = """\
WOM Tracker needs Python {needed} or newer; this is Python {found}.

Python {found} has no `zoneinfo`, and without it every time zone but US
Eastern silently becomes UTC - which moves every day boundary, every square
on the calendar and every window a recap is written for.

Install a newer Python and run it with that instead.\
"""


def check(version=None, out=None):
    """Return True if this interpreter will do, else print why and return False."""
    found = tuple((version or sys.version_info)[:2])
    if found >= MINIMUM:
        return True
    print(MESSAGE.format(needed=".".join(map(str, MINIMUM)),
                         found=".".join(map(str, found))),
          file=out or sys.stderr)
    return False


def require():
    """Stop before anything else runs, rather than misbehaving later."""
    if not check():
        raise SystemExit(1)
