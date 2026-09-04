"""What this needs from the interpreter running it.

Nothing in requirements.txt can say "you need Python 3.x": pip is a Python
program, so by the time it reads that file the interpreter has already been
chosen. `python_requires` in package metadata is the mechanism that enforces
it, and this is an application you clone rather than a package you install.
So the entry points ask here, on the way in.

Two things set the floor, and they are worth keeping straight because they
fail in opposite ways.

zoneinfo arrived in 3.9, and its absence is quiet. It is imported inside a
try/except so a missing time zone *database* degrades instead of crashing,
which is right when the database is missing and wrong when the interpreter is
simply too old: US Eastern keeps working off the built-in rules, every other
zone silently becomes UTC - moving every day boundary, every calendar square
and every recap window - and the admin page refuses each zone you type with
"not a time zone this machine knows", blaming the machine for the
interpreter's age.

The anthropic SDK requires 3.10, and its absence is loud: pip refuses to
install requirements.txt at all. That is the higher of the two, so it is the
floor. This file used to say 3.9 and note that "the README used to claim 3.10
for no reason anybody could point at" - the reason was in requirements.txt the
whole time, and nothing ever ran the install on 3.9 to find out. CI does now.
"""

import sys

MINIMUM = (3, 10)

MESSAGE = """\
WOM Tracker needs Python {needed} or newer; this is Python {found}.

Below 3.9 there is no `zoneinfo`, and without it every time zone but US
Eastern silently becomes UTC - which moves every day boundary, every square
on the calendar and every window a recap is written for. Below 3.10 the
anthropic SDK the recaps are written with will not install at all.

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
