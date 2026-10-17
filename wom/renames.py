"""Following an account to its new name.

The hiscores know a player only by name, so a rename looks to them like the
old account vanishing. Wise Old Man used to absorb this - it tracked name
changes and kept the same player id - and reading the hiscores directly
gives that up.

Two things notice instead. An hourly run that gets a 404 says so on the admin
page ("not on the hiscores - renamed?") for somebody to fix by hand. And
Dink, which reports the logged-in account's name with every event, can say
outright that the account behind a token is now called something else.

That report is not trusted on its own. A token is a URL, and a player can
paste it into an alt's client as easily as their main's, so "Dink says this
token is X now" is equally a rename and an alt. The hiscores tell the two
apart: after a rename the old name is gone and the new one is there; with an
alt, both are. Only the first is followed. The second is flagged and left.
"""

import logging

from .config import normalise_usernames
from .hiscores import HiscoresError, HiscoresNotFound

log = logging.getLogger(__name__)


def rename_player(database, config, old, new):
    """Move a player, and everything filed under their name, to a new name.

    The database first and then the settings, in that order because saving
    a roster prunes every stored player it does not name - so the rows must
    already answer to the new name when the roster starts using it.
    Returns True when the player was renamed.
    """
    old_key, new_key = old.lower(), new.lower()
    if old_key == new_key:
        return False
    if not database.rename_username(old_key, new_key, display_name=new):
        return False

    for section in ("usernames", "celebrities"):
        names = list(config.get(section, []) or [])
        if any(n.lower() == old_key for n in names):
            config[section] = normalise_usernames(
                new if n.lower() == old_key else n for n in names)
    for keyed in ("dink_tokens", "player_colors", "name_flags"):
        mapping = dict(config.get(keyed) or {})
        if old_key in mapping:
            value = mapping.pop(old_key)
            if keyed != "name_flags":
                mapping[new_key] = value
            config[keyed] = mapping
    config.save()
    log.info("renamed %s to %s", old, new)
    return True


def check_reported_name(database, config, reader, username, reported):
    """Act on Dink reporting `reported` for the account `username` owns.

    Returns the username to use from here on: the new one if the rename was
    confirmed and made, otherwise the old one. Never raises.
    """
    reported = " ".join(str(reported or "").split())
    if not reported or reported.lower() == username.lower():
        return username
    player = database.player_by_username(username)
    if player is None:
        return username
    try:
        try:
            reader.fetch(player["display_name"] or username)
            old_gone = False
        except HiscoresNotFound:
            old_gone = True
        new_found = False
        if old_gone:
            try:
                reader.fetch(reported)
                new_found = True
            except HiscoresNotFound:
                pass
    except HiscoresError as exc:
        log.info("could not check %s's reported name %r: %s", username,
                 reported, exc)
        return username

    if old_gone and new_found and rename_player(database, config, username,
                                                reported):
        return reported.lower()
    flags = dict(config.get("name_flags") or {})
    if flags.get(username.lower()) != reported:
        flags[username.lower()] = reported
        config["name_flags"] = flags
        config.save()
        log.warning("dink reports %r for %s's token; not a confirmed rename",
                    reported, username)
    return username
