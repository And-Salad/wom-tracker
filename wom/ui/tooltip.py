"""Hover tooltips for matplotlib charts embedded in Tk.

Charts register what each artist means with `add_hover(ax, artist, text)`, and
a ChartTooltip attached to the canvas does the hit-testing on mouse motion. The
tooltip is a borderless Tk window rather than a matplotlib annotation, so
following the cursor costs nothing - no figure redraw per mouse move.
"""

import logging
import tkinter as tk

from .. import theme

log = logging.getLogger(__name__)

# Where the tooltip sits relative to the pointer.
OFFSET_X, OFFSET_Y = 14, 14


def hover_text(title, detail):
    """The usual two-line tooltip: what it is, then the number."""
    return "{}\n{}".format(title, detail)


def add_hover(ax, artist, text):
    """Say what `artist` should show when the pointer is over it.

    `text` is a string, or a callable taking the hit details matplotlib
    reports - a line can then name the individual point under the cursor.
    """
    targets = getattr(ax, "_wom_hover", None)
    if targets is None:
        targets = []
        ax._wom_hover = targets
    targets.append((artist, text))


class ChartTooltip:
    """Shows the registered label for whatever artist is under the pointer."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.widget = canvas.get_tk_widget()
        self._window = None
        self._label = None
        self._text = None

        canvas.mpl_connect("motion_notify_event", self._on_move)
        canvas.mpl_connect("figure_leave_event", lambda _e: self.hide())
        self.widget.bind("<Leave>", lambda _e: self.hide(), add="+")

    # -- hit testing ------------------------------------------------------

    def _on_move(self, event):
        if event.x is None or event.y is None:
            self.hide()
            return
        text = self._hit(event)
        if text is None:
            self.hide()
            return
        self.show(text, event)

    def _hit(self, event):
        """The label of the topmost registered artist under the pointer.

        Every axes in the figure is searched rather than just `event.inaxes`:
        the axis icons hang below the axes and the legend sits above it, so
        both are outside it and would never be found otherwise.
        """
        figure = getattr(event.canvas, "figure", None)
        if figure is None:
            return None
        for ax in reversed(figure.axes):
            # Later artists are drawn on top, so search back to front.
            for artist, text in reversed(getattr(ax, "_wom_hover", ())):
                try:
                    hit, details = artist.contains(event)
                except Exception:
                    # Some artists cannot be tested before their first draw.
                    continue
                if not hit:
                    continue
                if not callable(text):
                    return text
                try:
                    return text(details)
                except Exception:
                    log.warning("hover label failed", exc_info=True)
                    return None
        return None

    # -- the window -------------------------------------------------------

    def show(self, text, event):
        if self._window is None:
            self._build()
        if text != self._text:
            self._label.config(text=text)
            self._text = text
        # Matplotlib measures y from the bottom of the canvas, Tk from the top.
        x = self.widget.winfo_rootx() + int(event.x) + OFFSET_X
        y = self.widget.winfo_rooty() + int(self.widget.winfo_height() - event.y) + OFFSET_Y
        self._window.wm_geometry("+{}+{}".format(x, y))
        self._window.deiconify()

    def hide(self):
        if self._window is not None:
            self._window.withdraw()

    def _build(self):
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)   # no title bar, no taskbar entry
        try:
            window.wm_attributes("-topmost", True)
        except tk.TclError:
            log.debug("tooltip could not be made topmost", exc_info=True)
        self._label = tk.Label(
            window, text="", justify=tk.LEFT, background=theme.RAISED,
            foreground=theme.INK, relief=tk.SOLID, borderwidth=1,
            padx=6, pady=3, font=("Segoe UI", 9))
        self._label.pack()
        window.withdraw()
        self._window = window
