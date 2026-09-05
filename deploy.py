"""Deploy what is committed, and publish what is deployed.

    py deploy.py                    check, deploy, push
    py deploy.py --dry-run          just say what it would do
    py deploy.py --skip-push        deploy without pushing (rarely what you want)
    py deploy.py --skip-tests       deploy without running them (see below)
    py deploy.py --off-main         allow deploying a branch that is not main

`fly deploy` builds from the working directory, not from git: the Dockerfile
copies wom/, assets/ and the two entry points straight off disk. So it will
cheerfully ship code that was never committed, and nothing afterwards can tell
you it did - the repo and the running app drift apart silently, which is how
the running site ended up ahead of its own history for most of a week.

This closes that by refusing to deploy anything git does not already know
about, then pushing what it deployed. The tests run first, because a deploy
that has to be rolled back costs more than the ninety seconds.

Prefer the Actions workflow - see .github/workflows/deploy.yml. It builds from
a checkout of one commit rather than from a directory that merely resembles
one, and confirms afterwards what the machines are running. This is the way to
deploy when GitHub is not answering, which is the only reason it still exists.
Both flags that weaken it - --skip-tests, --off-main - are spelled out rather
than defaulted, because the fallback path is exactly where a shortcut taken
once becomes the way it is always done.
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


def check_git(off_main=False):
    """Every reason the working tree is not safe to deploy from."""
    dirty = run(["git", "status", "--porcelain"])
    if dirty:
        raise Stop("there are uncommitted changes. Commit them first, or the "
                   "deployed code will exist nowhere but this machine:\n"
                   + dirty)

    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != "main" and not off_main:
        # This used to be a printed note, which is a thing you read after the
        # deploy has already been decided on. Deploying a branch is a real
        # thing to want - it is how you try a fix on the live machine - but it
        # leaves the running site on a commit that main does not contain, and
        # that is worth typing a word for rather than being told about.
        raise Stop("this is {}, not main. A branch deploy leaves the site on "
                   "a commit main does not have; pass --off-main if that is "
                   "what you meant.".format(branch))
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
    parser.add_argument("--skip-tests", action="store_true",
                        help="deploy without running the tests first. The "
                             "Actions workflow cannot skip them; this can, "
                             "which is the reason to say so out loud")
    parser.add_argument("--skip-push", action="store_true",
                        help="deploy without pushing, leaving the repo behind "
                             "what is live")
    parser.add_argument("--off-main", action="store_true",
                        help="allow deploying from a branch other than main")
    args = parser.parse_args(argv)

    try:
        print("checking the working tree...")
        branch, ahead = check_git(args.off_main)
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
                        "--image-label", sha,
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
