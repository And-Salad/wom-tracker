"""Pull a verified copy of the hosted database down to this machine.

    py backup.py                    into ./backups
    py backup.py --into D:\\wom      somewhere else
    py backup.py --keep 30          how many dated copies to keep

The database holds a year of history that cannot be re-fetched - Wise Old Man
does not keep snapshots forever - and summaries that cost real money to write.
The Fly volume it lives on has five days of snapshots and no copy anywhere
else, which is thin for that.

The copy is taken with SQLite's backup API *inside the container*, not by
reading the file: the app is writing to it, and a plain copy would leave recent
writes stranded in the write-ahead log beside it. What lands here is a single
consistent file, and this script opens it and counts it before believing it.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime

APP = "wom-tracker"
REMOTE_DB = "/data/wom.db"
REMOTE_COPY = "/tmp/wom-backup.db"
DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")

# What a healthy copy has in it. A backup that restores but is empty is worse
# than no backup, because it looks like one.
EXPECTED_TABLES = ("players", "snapshots", "metrics", "summaries")


def flyctl():
    """Where flyctl lives; the installer does not always reach PATH."""
    found = shutil.which("flyctl") or shutil.which("fly")
    if found:
        return found
    candidate = os.path.expanduser(r"~\.fly\bin\flyctl.exe")
    if os.path.exists(candidate):
        return candidate
    sys.exit("flyctl not found. Install it, or open a new terminal so it is on PATH.")


def run(args):
    """flyctl's exit code cannot be trusted on Windows.

    It prints "Error: The handle is invalid." and exits non-zero after
    commands that plainly succeeded, so every step here is judged by what it
    produced rather than by what it returned. The captured output is kept to
    show if an outcome check does fail.
    """
    done = subprocess.run(args, capture_output=True, text=True)
    return (done.stdout or "") + (done.stderr or "")


def take_remote_copy(fly, app):
    """Ask the container to write itself a consistent snapshot."""
    script = (
        "import sqlite3; "
        "src = sqlite3.connect('file:{db}?mode=ro', uri=True); "
        "dst = sqlite3.connect('{copy}'); "
        "src.backup(dst); dst.close(); src.close(); print('copied')"
    ).format(db=REMOTE_DB, copy=REMOTE_COPY)
    output = run([fly, "ssh", "console", "-a", app, "-C",
                  "python -c \"{}\"".format(script)])
    # The snippet says so itself; flyctl's own exit code proves nothing.
    if "copied" not in output:
        raise SystemExit(
            "could not take a copy inside the container: " + output.strip())


def fetch(fly, app, target):
    output = run([fly, "ssh", "sftp", "get", REMOTE_COPY, target, "-a", app])
    if not os.path.exists(target):
        raise SystemExit("the copy did not come down: " + output.strip())


def tidy_remote(fly, app):
    run([fly, "ssh", "console", "-a", app, "-C", "rm -f {}".format(REMOTE_COPY)])


def verify(path):
    """Open the copy and count it. Returns a line describing what is in it."""
    if not os.path.exists(path) or os.path.getsize(path) < 4096:
        raise SystemExit("the copy did not arrive, or arrived empty")
    conn = sqlite3.connect(path)
    try:
        health = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if health != "ok":
            raise SystemExit("the copy is corrupt: {}".format(health))
        counts = {}
        for table in EXPECTED_TABLES:
            counts[table] = conn.execute(
                "SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
    finally:
        conn.close()
    if not counts["players"]:
        raise SystemExit("the copy has no players in it - refusing to call it a backup")
    return counts


def rotate(folder, keep):
    """Keep the newest `keep` copies and delete the rest."""
    copies = sorted(f for f in os.listdir(folder)
                    if f.startswith("wom-") and f.endswith(".db"))
    for stale in copies[:-keep] if keep > 0 else []:
        os.remove(os.path.join(folder, stale))
        print("  removed {}".format(stale))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--app", default=APP)
    parser.add_argument("--into", default=DEFAULT_DIR,
                        help="where the copies go (default: ./backups)")
    parser.add_argument("--keep", type=int, default=14,
                        help="how many dated copies to keep (0 keeps all)")
    args = parser.parse_args(argv)

    fly = flyctl()
    os.makedirs(args.into, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = os.path.join(args.into, "wom-{}.db".format(stamp))

    print("taking a consistent copy inside the container...")
    take_remote_copy(fly, args.app)
    print("fetching it...")
    try:
        fetch(fly, args.app, target)
    finally:
        tidy_remote(fly, args.app)

    counts = verify(target)
    size = os.path.getsize(target) / 1e6
    print("\n{}  ({:.1f} MB)".format(target, size))
    print("  " + ", ".join("{} {:,}".format(name, n) for name, n in counts.items()))
    rotate(args.into, args.keep)
    print("\nRestore with:  fly ssh sftp shell -a {}  ->  put <file> /data/wom.db"
          .format(args.app))
    print("then: fly apps restart {}".format(args.app))
    return 0


if __name__ == "__main__":
    sys.exit(main())
