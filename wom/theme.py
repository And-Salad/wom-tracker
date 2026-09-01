"""The dark palette.

One definition, emitted as CSS custom properties, which the page styles itself
from and chartkit.js reads back for its axes, gridlines and tooltips.
"""

# Surfaces, from furthest back to nearest front.
BACKGROUND = "#16191d"   # the window/page behind everything
PANEL = "#1e2227"        # cards, sidebars, chart figures
RAISED = "#252a31"       # inputs, alternating table rows
LINE = "#333941"         # borders and separators

INK = "#e6e9ed"          # primary text
MUTED = "#99a2ad"        # captions, secondary text
ACCENT = "#6fa8d6"       # links and buttons

GRID = "#39414a"         # chart gridlines, read back by chartkit.js


def css_variables():
    """The whole palette as CSS custom properties.

    Every colour defined above appears here, and must: a constant that is
    not emitted is one nothing can reach. --grid was missing from this dict
    for exactly that reason, so chartkit.js had been drawing its gridlines
    from the hardcoded fallback beside the lookup and editing GRID here did
    nothing at all.
    """
    return {
        "--bg": BACKGROUND, "--card": PANEL, "--raised": RAISED,
        "--ink": INK, "--muted": MUTED, "--line": LINE, "--accent": ACCENT,
        "--grid": GRID,
    }
