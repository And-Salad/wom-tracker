"""A small colour picker: saturation/value gradient, hue strip, hex and RGB.

tkinter ships `colorchooser.askcolor`, but the native dialog has no hex field
and cannot show the palette these charts actually use, so this is hand-rolled.
"""

import colorsys
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

from .. import theme
from ..colors import DEFAULT_PALETTE, from_rgb, is_dark, normalise, to_rgb

GRADIENT_W, GRADIENT_H = 220, 180
HUE_W = 22


class ColorPicker(tk.Toplevel):
    """Modal colour chooser. `show()` returns '#rrggbb', or None if cancelled.

    `default` is offered as a "reset" button when given.
    """

    def __init__(self, master, initial="#3f7cac", title="Choose a colour", default=None):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self._default = normalise(default)
        self._syncing = False

        red, green, blue = to_rgb(normalise(initial) or "#3f7cac")
        hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        self._hue, self._sat, self._val = hue, saturation, value

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        pickers = ttk.Frame(outer)
        pickers.pack(fill=tk.X)
        self.gradient = tk.Canvas(pickers, width=GRADIENT_W, height=GRADIENT_H,
                                  highlightthickness=1, highlightbackground=theme.LINE,
                                  cursor="crosshair")
        self.gradient.pack(side=tk.LEFT)
        self.hue_strip = tk.Canvas(pickers, width=HUE_W, height=GRADIENT_H,
                                   highlightthickness=1, highlightbackground=theme.LINE,
                                   cursor="sb_v_double_arrow")
        self.hue_strip.pack(side=tk.LEFT, padx=(10, 0))

        for widget, handler in ((self.gradient, self._on_gradient),
                                (self.hue_strip, self._on_hue)):
            widget.bind("<Button-1>", handler)
            widget.bind("<B1-Motion>", handler)

        self._hue_image = ImageTk.PhotoImage(_hue_bar(HUE_W, GRADIENT_H))
        self.hue_strip.create_image(0, 0, anchor="nw", image=self._hue_image)
        self._hue_marker = self.hue_strip.create_rectangle(
            0, 0, HUE_W, 3, outline="#000", width=2)
        self._gradient_image = None
        self._gradient_id = self.gradient.create_image(0, 0, anchor="nw")
        self._gradient_marker = self.gradient.create_oval(
            0, 0, 0, 0, outline="#fff", width=2)

        # -- readouts -----------------------------------------------------
        fields = ttk.Frame(outer)
        fields.pack(fill=tk.X, pady=(12, 0))

        self.preview = tk.Label(fields, text="", width=10, height=2, relief=tk.SOLID,
                                borderwidth=1)
        self.preview.grid(row=0, column=0, rowspan=2, padx=(0, 12))

        ttk.Label(fields, text="Hex:").grid(row=0, column=1, sticky=tk.W)
        self.hex_var = tk.StringVar()
        hex_entry = ttk.Entry(fields, textvariable=self.hex_var, width=10)
        hex_entry.grid(row=0, column=2, sticky=tk.W, padx=(4, 0))
        hex_entry.bind("<Return>", lambda _e: self._apply_hex())
        hex_entry.bind("<FocusOut>", lambda _e: self._apply_hex())

        rgb_row = ttk.Frame(fields)
        rgb_row.grid(row=1, column=1, columnspan=2, sticky=tk.W, pady=(6, 0))
        self.rgb_vars = []
        for index, label in enumerate("RGB"):
            ttk.Label(rgb_row, text=label + ":").pack(side=tk.LEFT, padx=(0 if not index else 8, 2))
            var = tk.StringVar()
            spin = ttk.Spinbox(rgb_row, from_=0, to=255, width=5, textvariable=var,
                               command=self._apply_rgb)
            spin.pack(side=tk.LEFT)
            spin.bind("<Return>", lambda _e: self._apply_rgb())
            spin.bind("<FocusOut>", lambda _e: self._apply_rgb())
            self.rgb_vars.append(var)

        # -- palette ------------------------------------------------------
        ttk.Label(outer, text="Palette", foreground=theme.MUTED).pack(anchor=tk.W, pady=(12, 2))
        palette = ttk.Frame(outer)
        palette.pack(fill=tk.X)
        for color in DEFAULT_PALETTE:
            swatch = tk.Label(palette, background=color, width=2, height=1,
                              relief=tk.SOLID, borderwidth=1, cursor="hand2")
            swatch.pack(side=tk.LEFT, padx=(0, 4))
            swatch.bind("<Button-1>", lambda _e, c=color: self._set_color(c))

        # -- buttons ------------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="OK", command=self._accept).pack(side=tk.RIGHT, padx=6)
        if self._default:
            ttk.Button(buttons, text="Reset to default",
                       command=self._reset).pack(side=tk.LEFT)

        self._redraw_gradient()
        self._sync()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._accept())

    # -- interaction ------------------------------------------------------

    def show(self):
        """Run modally and return the chosen colour, or None."""
        self.grab_set()
        self.wait_window(self)
        return self.result

    @property
    def color(self):
        rgb = colorsys.hsv_to_rgb(self._hue, self._sat, self._val)
        return from_rgb([channel * 255 for channel in rgb])

    def _on_gradient(self, event):
        self._sat = min(1.0, max(0.0, event.x / float(GRADIENT_W - 1)))
        self._val = 1.0 - min(1.0, max(0.0, event.y / float(GRADIENT_H - 1)))
        self._sync()

    def _on_hue(self, event):
        self._hue = min(1.0, max(0.0, event.y / float(GRADIENT_H - 1)))
        self._redraw_gradient()
        self._sync()

    def _set_color(self, color):
        red, green, blue = to_rgb(color)
        self._hue, self._sat, self._val = colorsys.rgb_to_hsv(
            red / 255, green / 255, blue / 255)
        self._redraw_gradient()
        self._sync()

    def _apply_hex(self):
        color = normalise(self.hex_var.get())
        if color:
            self._set_color(color)
        else:
            self._sync()  # put the valid value back

    def _apply_rgb(self):
        try:
            channels = [int(float(var.get())) for var in self.rgb_vars]
        except (TypeError, ValueError):
            self._sync()
            return
        self._set_color(from_rgb(channels))

    def _reset(self):
        self.result = None
        self._reset_requested = True
        self.destroy()

    def _accept(self):
        self.result = self.color
        self.destroy()

    # -- painting ---------------------------------------------------------

    def _redraw_gradient(self):
        self._gradient_image = ImageTk.PhotoImage(
            _sv_square(self._hue, GRADIENT_W, GRADIENT_H))
        self.gradient.itemconfigure(self._gradient_id, image=self._gradient_image)

    def _sync(self):
        """Push the current colour into every readout and marker."""
        if self._syncing:
            return
        self._syncing = True
        try:
            color = self.color
            self.hex_var.set(color)
            for var, channel in zip(self.rgb_vars, to_rgb(color)):
                var.set(str(channel))
            self.preview.configure(background=color, text=color,
                                   foreground="#ffffff" if is_dark(color) else "#000000")
            x = self._sat * (GRADIENT_W - 1)
            y = (1.0 - self._val) * (GRADIENT_H - 1)
            self.gradient.coords(self._gradient_marker, x - 5, y - 5, x + 5, y + 5)
            hue_y = self._hue * (GRADIENT_H - 1)
            self.hue_strip.coords(self._hue_marker, 0, hue_y - 2, HUE_W, hue_y + 2)
        finally:
            self._syncing = False


def _sv_square(hue, width, height):
    """Saturation left-to-right, value top-to-bottom, at a fixed hue."""
    saturation = np.linspace(0.0, 1.0, width)[None, :]
    value = np.linspace(1.0, 0.0, height)[:, None]
    hsv = np.empty((height, width, 3), dtype=float)
    hsv[..., 0] = hue
    hsv[..., 1] = saturation
    hsv[..., 2] = value
    return Image.fromarray(_hsv_to_rgb(hsv))


def _hue_bar(width, height):
    hsv = np.empty((height, width, 3), dtype=float)
    hsv[..., 0] = np.linspace(0.0, 1.0, height)[:, None]
    hsv[..., 1] = 1.0
    hsv[..., 2] = 1.0
    return Image.fromarray(_hsv_to_rgb(hsv))


def _hsv_to_rgb(hsv):
    """Vectorised HSV -> 8-bit RGB; colorsys per pixel is far too slow here."""
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    i = np.floor(hue * 6.0)
    f = hue * 6.0 - i
    p = value * (1.0 - saturation)
    q = value * (1.0 - f * saturation)
    t = value * (1.0 - (1.0 - f) * saturation)
    i = i.astype(int) % 6
    red = np.choose(i, [value, q, p, p, t, value])
    green = np.choose(i, [t, value, value, q, p, p])
    blue = np.choose(i, [p, p, t, value, value, q])
    return (np.stack([red, green, blue], axis=-1) * 255).astype(np.uint8)


def ask_color(master, initial="#3f7cac", title="Choose a colour", default=None):
    """Open the picker; returns (chosen, reset_requested)."""
    dialog = ColorPicker(master, initial=initial, title=title, default=default)
    chosen = dialog.show()
    return chosen, bool(getattr(dialog, "_reset_requested", False))
