"""Deploy what is committed, and publish what is deployed.

    py deploy.py                    check, deploy, push
    py deploy.py --dry-run          just say what it would do
    py deploy.py --skip-push        deploy without pushing (rarely what you want)

`fly deploy` builds from the working directory, not from git: the Dockerfile
copies wom/, assets/ and the two entry points straight off disk. So it will
cheerfully ship code that was never committed, and nothing afterwards can tell
you it did - the repo and the running app drift apart silently, which is how
the running site ended up ahead of its own history for most of a week.

This closes that by refusing to deploy anything git does not already know
about, then pushing what it deployed. The tests run first, because a deploy
that has to be rolled back costs more than the ninety seconds.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The files the image is built from. A change anywhere else - the README, the
# tests - does not need a deploy, but it does need committing before one.
SHIPPED = ("wom", "assets", "web_app.py", "wom_tracker.py", "Dockerfile",
           "fly.toml", "requirements.txt")


class Stop(Exception):
    """A reason not to deploy, phrased for a person."""


def run(command, capture=True):
    """Run a command in the project directory. Returns its stdout."""
    done = subprocess.run(command, cwd=HERE, capture_output=capture, text=True)
    if done.returncode != 0:
        raise Stop("{} failed:\n{}".format(
            " ".join(command), (done.stderr or done.stdout or "").strip()))
    return (done.stdout or "").strip()


def flyctl():
    """Where flyctl lives, since it is not always on PATH."""
    found = shutil.which("flyctl") or shutil.which("fly")
    if found:
        return found
    fallback = os.path.expanduser(r"~\.fly\bin\flyctl.exe")
    if os.path.exists(fallback):
        return fallback
    raise Stop("flyctl is not installed, or not on PATH")


def check_git():
    """Every reason the working tree is not safe to deploy from."""
    dirty = run(["git", "status", "--porcelain"])
    if dirty:
        raise Stop("there are uncommitted changes. Commit them first, or the "
                   "deployed code will exist nowhere but this machine:\n"
                   + dirty)

    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != "main":
        print("  note: deploying from {}, not main".format(branch))

    # An unpushed commit is not as bad as an uncommitted change - it exists in
    # git - but it still means the repo does not show what is running.
    #
    # The fetch is inside the try as well as the count. A repository with no
    # origin fails at the fetch, and it was the one command here whose failure
    # was not caught - so "there is no upstream yet", which the line below
    # exists to tolerate, refused the deploy instead.
    try:
        run(["git", "fetch", "--quiet", "origin"])
        ahead = run(["git", "rev-list", "--count", "origin/{}..HEAD".format(branch)])
    except Stop:
        ahead = "0"          # no upstream yet; the push will set one
    return branch, int(ahead or 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="run the checks and stop")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-push", action="store_true")
    args = parser.parse_args(argv)

    try:
        print("checking the working tree...")
        branch, ahead = check_git()
        head = run(["git", "log", "-1", "--format=%h %s"])
        print("  clean, at {}".format(head))
        if ahead:
            print("  {} commit{} not yet pushed".format(
                ahead, "" if ahead == 1 else "s"))

        if not args.skip_tests:
            print("running the tests...")
            run([sys.executable, "-m", "pytest", "-q"], capture=True)
            print("  passed")

        if args.dry_run:
            print("dry run: would deploy and push")
            return 0

        print("deploying...")
        # The commit goes into the image so the running app can say which one
        # it is - the admin page prints it. Safe to bake in here because the
        # tree is checked clean above, so HEAD really is what is being built.
        sha = run(["git", "rev-parse", "HEAD"])
        subprocess.run([flyctl(), "deploy", "--now",
                        "--build-arg", "GIT_SHA=" + sha],
                       cwd=HERE, check=True)

        if args.skip_push:
            print("not pushing, as asked - the repo is now behind what is live")
            return 0

        print("pushing...")
        run(["git", "push", "origin", branch])
        print("done: {} is deployed and pushed".format(head))
        return 0
    except Stop as stop:
        print("\nstopped: {}".format(stop), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as failed:
        print("\nstopped: {} exited {}".format(
            failed.cmd[0], failed.returncode), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
