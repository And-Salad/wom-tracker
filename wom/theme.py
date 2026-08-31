"""The dark palette.

One definition, emitted as CSS custom properties, which the page styles itself
from and charts.js reads back for its axes, gridlines and tooltips.
"""

# Surfaces, from furthest back to nearest front.
BACKGROUND = "#16191d"   # the window/page behind everything
PANEL = "#1e2227"        # cards, sidebars, chart figures
RAISED = "#252a31"       # inputs, alternating table rows
LINE = "#333941"         # borders and separators

INK = "#e6e9ed"          # primary text
MUTED = "#99a2ad"        # captions, secondary text
ACCENT = "#6fa8d6"       # links and buttons

GRID = "#39414a"         # chart gridlines
EXCLUDED = "#6d7681"     # a player left out of the comparison


def css_variables():
    """The same palette as CSS custom properties for the web dashboard."""
    return {
        "--bg": BACKGROUND, "--card": PANEL, "--raised": RAISED,
        "--ink": INK, "--muted": MUTED, "--line": LINE, "--accent": ACCENT,
    }
