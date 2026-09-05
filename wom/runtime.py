"""What this needs from the interpreter running it.

Nothing in requirements.txt can say "you need Python 3.x": pip is a Python
program, so by the time it reads that file the interpreter has already been
chosen. `python_requires` in package metadata is the mechanism that enforces
it, and this is an application you clone rather than a package you install.
So the entry points ask here, on the way in.

The floor is 3.12, and it is worth being straight about why, because the
reason changed shape.

It used to be a fact about the dependencies. zoneinfo arrived in 3.9 and the
anthropic SDK wanted 3.10, so 3.10 was the higher of two numbers this code
did not choose. That is still all the packages ask for - as of this writing
anthropic and requests want 3.10, flask and waitress 3.9. Read literally, the
old floor is still correct.

What changed is that 3.10 stopped receiving security fixes on 31 October
2026. So this is a decision now rather than a constraint: not "the code will
not run" but "nobody should be asked to run this on an interpreter that is no
longer patched". Worth naming, because a floor that looks like a technical
fact goes unquestioned - and this one wants revisiting each time a version
ages out rather than being inherited.

3.12 rather than 3.11 because 3.12 is what the Dockerfile ships. The floor,
the image and the machine this is developed on are one version now, which
closes the gap where something works in two of the three. 3.12 is supported
until October 2028.

The check itself stays for the reason it always existed: below the floor the
failures are quiet ones. Without zoneinfo, US Eastern keeps working off the
built-in rules and every other zone silently becomes UTC - moving every day
boundary, every calendar square and every recap window - while the admin page
refuses each zone you type, blaming the machine for the interpreter's age.
"""

import sys

MINIMUM = (3, 12)

MESSAGE = """\
WOM Tracker needs Python {needed} or newer; this is Python {found}.

{needed} is what the Docker image ships and what the tests run on. Older
versions may well work - the packages themselves ask only for 3.10 - but
nothing here is tested on them, and 3.10 and earlier no longer get security
fixes.

Below 3.9 it will not work at all: there is no `zoneinfo`, so every time zone
but US Eastern silently becomes UTC, which moves every day boundary, every
square on the calendar and every window a recap is written for.

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
