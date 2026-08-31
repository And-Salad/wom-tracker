"""Per-player chart colours.

Every player gets a colour from the default palette by their position in the
tracked list. Picking one by hand stores an override in the config, keyed by
lowercase username, so it survives restarts and follows the player around every
chart and the sidebar swatch.
"""

# Chosen to stay distinguishable when stacked next to each other.
DEFAULT_PALETTE = (
    "#3f7cac", "#d1495b", "#66a182", "#edae49", "#8d6a9f",
    "#2e4057", "#c9814b", "#4f9d9d", "#b56576", "#7d8c3a",
)


def default_color(index):
    return DEFAULT_PALETTE[index % len(DEFAULT_PALETTE)]


def player_color(config, username, index=0):
    """The colour to draw this player in: their override, else the palette."""
    overrides = config.get("player_colors") or {}
    chosen = overrides.get(str(username).lower())
    return normalise(chosen) or default_color(index)


def set_player_color(config, username, color):
    """Store an override, or clear it by passing None."""
    overrides = dict(config.get("player_colors") or {})
    key = str(username).lower()
    if color is None:
        overrides.pop(key, None)
    else:
        overrides[key] = normalise(color)
    config["player_colors"] = overrides
    config.save()
    return overrides


def normalise(value):
    """Return '#rrggbb' for anything hex-shaped, else None."""
    if not value:
        return None
    text = str(value).strip().lstrip("#")
    if len(text) == 3 and all(c in "0123456789abcdefABCDEF" for c in text):
        text = "".join(c * 2 for c in text)
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        return None
    return "#" + text.lower()


def to_rgb(color):
    """'#rrggbb' -> (r, g, b) as 0-255 ints."""
    color = normalise(color) or "#000000"
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def from_rgb(rgb):
    """(r, g, b) -> '#rrggbb', clamping anything out of range."""
    return "#" + "".join("{:02x}".format(max(0, min(255, int(round(c))))) for c in rgb)


def is_dark(color):
    """True when white text reads better than black on this colour."""
    red, green, blue = to_rgb(color)
    # Rec. 601 luma, the usual quick contrast check.
    return (0.299 * red + 0.587 * green + 0.114 * blue) < 140
