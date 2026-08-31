"""One running copy of the desktop app, not several.

The window hides to the tray rather than closing, so launching it again looks
like nothing happened - and you end up with two schedulers quietly updating the
same database. A listening socket on the loopback interface is the lock: the
first copy binds the port and holds it for as long as it runs, and the second
finds the port taken, asks whoever holds it to show their window, and exits.

A socket is used rather than a lock file because the operating system releases
it when the process dies. A stale lock file after a crash would leave the app
refusing to start with no obvious way to fix it.
"""

import logging
import socket
import threading

log = logging.getLogger(__name__)

# Chosen from the dynamic/private range. Nothing well-known lives here, and the
# handshake below means another program squatting on it is detected rather than
# mistaken for a second copy of this app.
LOCK_PORT = 47615
GREETING = b"WOM-TRACKER/1"
SHOW = b"SHOW"


class InstanceLock:
    """Held by the running copy; `taken` says another copy already has it."""

    def __init__(self, on_show=None, port=LOCK_PORT):
        self.port = port
        self.on_show = on_show
        self.taken = False          # someone else is already running
        self._socket = None
        self._thread = None
        self._stop = False

    def acquire(self):
        """Try to become the one running copy. Returns True if we are it."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Deliberately no SO_REUSEADDR: the point is to fail when taken.
            listener.bind(("127.0.0.1", self.port))
            listener.listen(4)
        except OSError:
            listener.close()
            self.taken = _greet(self.port)
            if not self.taken:
                # The port is busy but not with us. Better to run unlocked than
                # to refuse to start over someone else's software.
                log.warning("port %s is in use by something else; running "
                            "without the single-instance lock", self.port)
            return not self.taken

        self._socket = listener
        self._thread = threading.Thread(target=self._serve, name="wom-instance",
                                        daemon=True)
        self._thread.start()
        return True

    def _serve(self):
        while not self._stop:
            try:
                connection, _address = self._socket.accept()
            except OSError:
                return                      # closed by release()
            with connection:
                try:
                    connection.sendall(GREETING)
                    connection.settimeout(2)
                    if connection.recv(16).strip() == SHOW and self.on_show:
                        self.on_show()
                except OSError:
                    continue

    def release(self):
        self._stop = True
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None


def _greet(port):
    """Ask the copy already running to show itself. True if one answered."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as link:
            link.settimeout(2)
            if link.recv(len(GREETING)) != GREETING:
                return False            # not us - some other program
            link.sendall(SHOW)
            return True
    except OSError:
        return False
