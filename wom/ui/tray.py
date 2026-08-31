"""Optional system-tray icon so the app can sit in the background.

Falls back to None when pystray/Pillow are unavailable, in which case the main
window simply stays open instead of hiding.
"""

import logging
import threading

log = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw
    AVAILABLE = True
except Exception:  # pragma: no cover - depends on the install
    pystray = None
    AVAILABLE = False


def make_image(size=64):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, size - 2, size - 2), fill=(63, 124, 172, 255))
    draw.text((size // 2 - 9, size // 2 - 12), "W", fill=(255, 255, 255, 255))
    draw.text((size // 2 - 1, size // 2 - 2), "OM", fill=(230, 240, 250, 255))
    return image


class TrayIcon:
    """Wraps pystray so the caller does not care whether it exists."""

    def __init__(self, title, on_show, on_update, on_quit):
        self._icon = None
        self._thread = None
        if not AVAILABLE:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Open WOM Tracker", lambda *_: on_show(), default=True),
            pystray.MenuItem("Update now", lambda *_: on_update()),
            pystray.MenuItem("Quit", lambda *_: on_quit()),
        )
        self._icon = pystray.Icon("wom_tracker", make_image(), title, menu)

    @property
    def active(self):
        return self._icon is not None

    def start(self):
        if not self._icon or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="wom-tray", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._icon.run()
        except Exception:
            log.exception("tray icon stopped")

    def notify(self, message, title="WOM Tracker"):
        if not self._icon:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            log.debug("tray notification not supported", exc_info=True)

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                log.debug("tray stop failed", exc_info=True)
