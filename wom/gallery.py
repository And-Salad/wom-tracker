"""Screenshots that came with a death or a pet drop.

Dink can attach one to most of its notifications. We take them for two kinds
and refuse them for every other, because a public endpoint that accepts
arbitrary bytes should accept as few as it can get away with, and because a
level-up screenshot is not something anybody would go and look at.

The bytes go on the volume rather than into the database. A few hundred
megabytes of PNG inside `wom.db` would ride along on every backup pull, and
these are decorative - losing them costs a picture, where losing a snapshot
costs history that cannot be fetched again. `backup.py` does not carry them,
and that is the deliberate answer rather than an oversight.

Nothing a client sends is used as a path. The file is named by the digest of
its own contents, which also means the same screenshot delivered twice is
stored once.
"""

import hashlib
import logging
import os

from .config import data_dir

log = logging.getLogger(__name__)

# The two kinds worth looking at, and so the only two whose images we accept.
IMAGE_KINDS = ("death", "pet")

# Dink builds for Discord's 8MB ceiling and defaults to full scale. RuneLite
# screenshots land well under this; the cap is here to bound what one leaked
# URL can make us read, not because a real one comes close.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# How many we keep per kind. The page shows ten; the rest are held so a feed
# can look further back without needing anyone to have played since.
KEEP_PER_KIND = 40

# The backstop, in case a run of enormous screenshots outpaces the count. The
# volume is a gigabyte and the database is a couple of megabytes, so this is
# generous rather than tight - it exists to have a ceiling at all.
TOTAL_BUDGET_BYTES = 250 * 1024 * 1024

# What the first bytes of a file have to be. The client's content type is not
# consulted: it is the one thing in the request nobody had to prove, and a
# mislabelled file served back with the label it claimed is how a picture
# becomes a script.
MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpeg", "image/jpeg"),
)

MIME = {fmt: mime for _prefix, fmt, mime in MAGIC}


def folder():
    return os.path.join(data_dir(), "gallery")


def sniff(data):
    """The format these bytes actually are, or None if we will not take them."""
    for prefix, fmt, _mime in MAGIC:
        if data.startswith(prefix):
            return fmt
    return None


def path_for(digest, fmt):
    return os.path.join(folder(), "{}.{}".format(digest, fmt))


def store(database, username, kind, happened_at, data, event_id=None,
          caption=None):
    """Keep one screenshot. Returns its digest, or None if it was refused."""
    if kind not in IMAGE_KINDS:
        return None
    if not data or len(data) > MAX_IMAGE_BYTES:
        log.info("gallery: refusing %d bytes from %s", len(data or b""), username)
        return None
    fmt = sniff(data)
    if fmt is None:
        log.warning("gallery: %s sent something that is not an image", username)
        return None

    digest = hashlib.sha256(data).hexdigest()
    os.makedirs(folder(), exist_ok=True)
    path = path_for(digest, fmt)
    if not os.path.exists(path):
        # Written beside and moved into place, so a half-written file is never
        # one the gallery will try to serve.
        temporary = path + ".part"
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    database.record_image(digest, username, kind, fmt, len(data), happened_at,
                          event_id=event_id, caption=caption)
    prune(database)
    return digest


def prune(database):
    """Drop what is past the keep count, then what is past the budget."""
    removed = 0
    for kind in IMAGE_KINDS:
        for row in database.surplus_images(kind, KEEP_PER_KIND):
            removed += _forget(database, row)
    while database.image_bytes_stored() > TOTAL_BUDGET_BYTES:
        oldest = database.oldest_images(limit=20)
        if not oldest:
            break
        for row in oldest:
            removed += _forget(database, row)
    if removed:
        log.info("gallery: removed %d old image%s", removed,
                 "" if removed == 1 else "s")
    return removed


def _forget(database, row):
    """Delete one image's file and its row, in that order.

    File first: a row with no file is a broken picture, a file with no row is
    bytes nothing will ever look at or count.
    """
    try:
        os.remove(path_for(row["digest"], row["format"]))
    except OSError:
        pass
    database.forget_image(row["digest"])
    return 1


def caption_for(kind, payload):
    """The line under a picture, or an empty string."""
    extra = payload.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    if kind == "pet":
        name = extra.get("petName")
        if extra.get("duplicate"):
            return "{} again".format(name) if name else "a duplicate"
        return name or "a pet"
    if kind == "death":
        lost = extra.get("valueLost")
        if extra.get("isPvp"):
            return "killed by a player"
        try:
            return "lost {:,.0f} gp".format(float(lost)) if lost else "died"
        except (TypeError, ValueError):
            return "died"
    return ""
