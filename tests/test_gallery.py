"""Screenshots: what we take, what we refuse, and what we throw away.

This is the only place the app accepts arbitrary bytes from the internet, so
most of these are about refusing things rather than keeping them.
"""

import io as _io
import os

from wom import gallery

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend this is pixels"
JPEG = b"\xff\xd8\xff" + b"pretend this is pixels too"
WHEN = "2026-09-03T21:15:00.000000Z"


def shot(data, name="shot.png"):
    return {"file": (_io.BytesIO(data), name)}


# -- what counts as an image ----------------------------------------------

def test_the_bytes_decide_the_format_not_the_name():
    assert gallery.sniff(PNG) == "png"
    assert gallery.sniff(JPEG) == "jpeg"


def test_anything_that_is_not_an_image_is_refused(db, player):
    """A mislabelled file served back with the label it claimed is how a
    picture becomes a script."""
    for pretender in (b"<script>alert(1)</script>", b"GIF89a", b"%PDF-1.4",
                      b"<!doctype html>", b""):
        assert gallery.store(db, player["username"], "death", WHEN,
                             pretender) is None
    assert db.images(kind="death") == []


def test_a_kind_the_gallery_does_not_show_keeps_no_picture(db, player):
    """Every kind we accept is more that a leaked URL can push at us."""
    for kind in ("collection", "level", "quest", "kill_count"):
        assert gallery.store(db, player["username"], kind, WHEN, PNG) is None
    assert db.image_bytes_stored() == 0


def test_an_oversized_image_is_refused(db, player):
    huge = b"\x89PNG\r\n\x1a\n" + b"x" * gallery.MAX_IMAGE_BYTES
    assert gallery.store(db, player["username"], "death", WHEN, huge) is None


# -- storing it ------------------------------------------------------------

def test_a_screenshot_is_kept_and_named_by_its_contents(db, player):
    digest = gallery.store(db, player["username"], "pet", WHEN, PNG,
                           caption="a pet")
    assert digest and len(digest) == 64
    assert os.path.exists(gallery.path_for(digest, "png"))
    row = db.image(digest)
    assert row["kind"] == "pet" and row["format"] == "png"
    assert row["bytes"] == len(PNG) and row["caption"] == "a pet"


def test_the_same_picture_twice_is_stored_once(db, player):
    first = gallery.store(db, player["username"], "pet", WHEN, PNG)
    second = gallery.store(db, player["username"], "pet",
                           "2026-09-03T22:00:00.000000Z", PNG)
    assert first == second
    assert len(db.images(kind="pet", limit=50)) == 1


def test_nothing_the_client_sent_becomes_a_path(db, player):
    """The digest is the whole file name, so a crafted one cannot escape."""
    digest = gallery.store(db, player["username"], "death", WHEN, PNG)
    assert os.path.basename(gallery.path_for(digest, "png")) == digest + ".png"


def test_a_half_written_file_is_never_served(db, player, monkeypatch):
    """Written beside and moved into place.

    Fresh bytes on purpose: a digest already on disk is not written again,
    which is the deduplication working and would hide what this is testing.
    """
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append(a), real(a, b))[1])
    gallery.store(db, player["username"], "death", WHEN, PNG + b"unique-here")
    assert seen and seen[0].endswith(".part")


def test_bytes_already_on_disk_are_not_written_again(db, player, monkeypatch):
    """The digest names the file, so an identical screenshot is one file."""
    gallery.store(db, player["username"], "death", WHEN, PNG + b"twice")
    wrote = []
    real_open = open
    monkeypatch.setattr("builtins.open",
                        lambda *a, **k: (wrote.append(a[0]), real_open(*a, **k))[1])
    gallery.store(db, player["username"], "death",
                  "2026-09-03T23:00:00.000000Z", PNG + b"twice")
    assert not any(str(name).endswith(".part") for name in wrote)


# -- throwing it away ------------------------------------------------------

def test_only_the_newest_of_each_kind_are_kept(db, player, monkeypatch):
    monkeypatch.setattr(gallery, "KEEP_PER_KIND", 3)
    for n in range(6):
        gallery.store(db, player["username"], "death",
                      "2026-09-03T2{}:00:00.000000Z".format(n),
                      PNG + bytes([n]))
    rows = db.images(kind="death", limit=50)
    assert len(rows) == 3, "the keep count is a ceiling, not a suggestion"
    assert rows[0]["happened_at"].startswith("2026-09-03T25")


def test_the_file_goes_when_the_row_does(db, player, monkeypatch):
    """Bytes nothing will ever look at still take up the volume."""
    monkeypatch.setattr(gallery, "KEEP_PER_KIND", 1)
    first = gallery.store(db, player["username"], "pet",
                          "2026-09-03T20:00:00.000000Z", PNG)
    gallery.store(db, player["username"], "pet",
                  "2026-09-03T21:00:00.000000Z", JPEG)
    assert not os.path.exists(gallery.path_for(first, "png"))
    assert db.image(first) is None


def test_a_budget_is_enforced_even_if_the_count_is_not_reached(db, player,
                                                               monkeypatch):
    """A run of enormous screenshots would otherwise sit under the keep count
    and over the volume."""
    monkeypatch.setattr(gallery, "KEEP_PER_KIND", 100)
    monkeypatch.setattr(gallery, "TOTAL_BUDGET_BYTES", len(PNG) * 3)
    for n in range(8):
        gallery.store(db, player["username"], "death",
                      "2026-09-03T2{}:00:00.000000Z".format(n), PNG + bytes([n]))
    assert db.image_bytes_stored() <= gallery.TOTAL_BUDGET_BYTES


def test_removing_a_file_that_is_already_gone_is_not_an_error(db, player,
                                                              monkeypatch):
    monkeypatch.setattr(gallery, "KEEP_PER_KIND", 1)
    first = gallery.store(db, player["username"], "pet",
                          "2026-09-03T20:00:00.000000Z", PNG)
    os.remove(gallery.path_for(first, "png"))
    gallery.store(db, player["username"], "pet",
                  "2026-09-03T21:00:00.000000Z", JPEG)
    assert db.image(first) is None


# -- captions --------------------------------------------------------------

def test_a_caption_says_what_happened():
    assert gallery.caption_for("pet", {"extra": {"petName": "Ikkle hydra"}}) \
        == "Ikkle hydra"
    assert gallery.caption_for("pet", {"extra": {"petName": "Pet chaos elemental",
                                                 "duplicate": True}}) \
        == "Pet chaos elemental again"
    assert gallery.caption_for("pet", {"extra": {}}) == "a pet"
    assert gallery.caption_for("death", {"extra": {"valueLost": 1234567}}) \
        == "lost 1,234,567 gp"
    assert gallery.caption_for("death", {"extra": {"isPvp": True}}) \
        == "killed by a player"
    assert gallery.caption_for("death", {"extra": {"valueLost": "odd"}}) == "died"
    assert gallery.caption_for("collection", {}) == ""


def test_a_budget_that_cannot_be_met_still_stops(db, player, monkeypatch):
    """Over budget with nothing left to delete has to end the loop.

    It should not be reachable - bytes counted with no rows to count them
    from - but a loop that deletes until a number falls has to have an exit
    that does not depend on the number falling.
    """
    gallery.store(db, player["username"], "pet", WHEN, PNG + b"budget")
    monkeypatch.setattr(gallery, "KEEP_PER_KIND", 100)
    monkeypatch.setattr(gallery, "TOTAL_BUDGET_BYTES", 0)
    monkeypatch.setattr(db, "oldest_images", lambda limit=50: [])
    assert gallery.prune(db) == 0, "returned rather than spinning"
