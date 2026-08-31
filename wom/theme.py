"""The dark palette, shared by the desktop window, the charts and the web page.

One definition so a chart PNG never lands on a background it does not match -
the web dashboard embeds the very same figures the Tk app draws.
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

# Selection colours for the Tk player list.
SELECT_BG = "#2f6690"
SELECT_INK = "#ffffff"


def matplotlib_rc():
    """rcParams that put a figure on PANEL with legible axes."""
    return {
        "figure.facecolor": PANEL,
        "figure.edgecolor": PANEL,
        "savefig.facecolor": PANEL,
        "savefig.edgecolor": PANEL,
        "axes.facecolor": PANEL,
        "axes.edgecolor": LINE,
        "axes.labelcolor": INK,
        "axes.titlecolor": INK,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": GRID,
        "legend.facecolor": PANEL,
        "legend.edgecolor": LINE,
        "legend.labelcolor": INK,
    }


def apply_matplotlib():
    """Set the dark defaults for every figure this process draws."""
    import matplotlib
    matplotlib.rcParams.update(matplotlib_rc())


def apply_ttk(widget):
    """Restyle ttk for the desktop window.

    'clam' is the only stock theme that lets colours be set on every widget;
    'vista' hard-codes its own and would leave white patches behind.
    """
    from tkinter import ttk
    style = ttk.Style(widget)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    widget.configure(background=BACKGROUND)
    style.configure(".", background=BACKGROUND, foreground=INK,
                    fieldbackground=RAISED, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=BACKGROUND,
                    focuscolor=ACCENT, insertcolor=INK)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=INK)
    style.configure("TLabelframe", background=BACKGROUND, foreground=INK,
                    bordercolor=LINE)
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground=MUTED)
    style.configure("TButton", background=RAISED, foreground=INK, bordercolor=LINE)
    style.map("TButton",
              background=[("active", SELECT_BG), ("pressed", SELECT_BG)],
              foreground=[("active", SELECT_INK)])
    style.configure("TCheckbutton", background=BACKGROUND, foreground=INK)
    style.map("TCheckbutton", background=[("active", BACKGROUND)])
    style.configure("TEntry", fieldbackground=RAISED, foreground=INK,
                    insertcolor=INK, bordercolor=LINE)
    style.configure("TSpinbox", fieldbackground=RAISED, foreground=INK,
                    arrowcolor=INK, bordercolor=LINE)
    style.configure("TCombobox", fieldbackground=RAISED, foreground=INK,
                    arrowcolor=INK, bordercolor=LINE, background=RAISED)
    style.map("TCombobox",
              fieldbackground=[("readonly", RAISED)],
              foreground=[("readonly", INK)],
              selectbackground=[("readonly", RAISED)],
              selectforeground=[("readonly", INK)])
    style.configure("TNotebook", background=BACKGROUND, bordercolor=LINE)
    style.configure("TNotebook.Tab", background=RAISED, foreground=MUTED,
                    padding=(12, 5), bordercolor=LINE)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL)], foreground=[("selected", INK)])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=INK, bordercolor=LINE)
    style.configure("Treeview.Heading", background=RAISED, foreground=MUTED,
                    bordercolor=LINE)
    style.map("Treeview.Heading", background=[("active", LINE)])
    style.map("Treeview",
              background=[("selected", SELECT_BG)],
              foreground=[("selected", SELECT_INK)])
    style.configure("TScrollbar", background=RAISED, troughcolor=BACKGROUND,
                    bordercolor=LINE, arrowcolor=MUTED)
    style.configure("TPanedwindow", background=BACKGROUND)
    style.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=RAISED, bordercolor=LINE)
    return style


def css_variables():
    """The same palette as CSS custom properties for the web dashboard."""
    return {
        "--bg": BACKGROUND, "--card": PANEL, "--raised": RAISED,
        "--ink": INK, "--muted": MUTED, "--line": LINE, "--accent": ACCENT,
    }
