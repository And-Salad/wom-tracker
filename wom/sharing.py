"""Running the dashboard and its tunnel from inside the desktop app.

Both used to be separate console windows to keep alive by hand. Neither needs
to be: waitress can be driven from a thread, and cloudflared is a child process
whose output we can read. This module owns both lifecycles and reports what it
is doing in plain sentences, so the UI layer is only buttons and a label.
"""

import logging
import os
import re
import shutil
import socket
import subprocess
import threading

log = logging.getLogger(__name__)

LOCAL_ONLY = "127.0.0.1"
EVERYWHERE = "0.0.0.0"
DEFAULT_PORT = 8000

# cloudflared prints the address it was given somewhere in its banner.
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# winget puts it here, and a machine that installed it this way often has no
# PATH entry for it until the shell is restarted.
KNOWN_CLOUDFLARED = (
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
)


def find_cloudflared():
    """The cloudflared binary, or None if it is not installed."""
    found = shutil.which("cloudflared")
    if found:
        return found
    for path in KNOWN_CLOUDFLARED:
        if os.path.exists(path):
            return path
    return None


def local_addresses(port):
    """Addresses worth showing so people know where to point a browser."""
    out = ["http://localhost:{}".format(port)]
    try:
        host = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        host = []
    out += ["http://{}:{}".format(ip, port) for ip in host
            if not ip.startswith("127.")]
    return out


def port_is_free(port, host=LOCAL_ONLY):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


class WebServer:
    """The read-only dashboard, served from a thread of this process."""

    def __init__(self, on_event=None):
        self.on_event = on_event or (lambda _message: None)
        self.host = LOCAL_ONLY
        self.port = DEFAULT_PORT
        self._server = None
        self._thread = None

    @property
    def running(self):
        return self._server is not None

    @property
    def shared_on_network(self):
        return self.running and self.host == EVERYWHERE

    def start(self, host=LOCAL_ONLY, port=DEFAULT_PORT):
        if self.running:
            return
        if not port_is_free(port, host):
            raise RuntimeError(
                "port {} is already in use - stop whatever is using it, or "
                "pick another port".format(port))
        from waitress import create_server

        from .web import create_app
        # create_server rather than serve(): serve() blocks forever with no
        # way back, and stopping cleanly matters when this lives in the app.
        self._server = create_server(create_app(), host=host, port=port,
                                     threads=8)
        self.host, self.port = host, port
        self._thread = threading.Thread(target=self._run, name="wom-web",
                                        daemon=True)
        self._thread.start()
        self.on_event("dashboard running on {}:{}".format(host, port))

    def _run(self):
        try:
            self._server.run()
        except Exception as exc:                    # pragma: no cover
            log.exception("web server stopped")
            self.on_event("dashboard stopped: {}".format(exc))

    def stop(self):
        if not self.running:
            return
        try:
            # Let the worker threads finish first. Closing the server out from
            # under a request in flight leaves that thread poking a socket
            # that has already gone, and waitress logs a WinError 10038
            # traceback for each one - noise on every shutdown under load.
            self._server.task_dispatcher.shutdown(cancel_pending=True, timeout=5)
        except Exception:
            log.debug("draining the web server's task queue", exc_info=True)
        try:
            self._server.close()
        except Exception:
            log.exception("closing the web server")
        self._server = None
        self._thread = None
        self.on_event("dashboard stopped")

    def urls(self):
        if not self.running:
            return []
        if self.host == LOCAL_ONLY:
            return ["http://localhost:{}".format(self.port)]
        return local_addresses(self.port)


class Tunnel:
    """A cloudflared quick tunnel pointing at the local dashboard."""

    def __init__(self, on_event=None, on_url=None):
        self.on_event = on_event or (lambda _message: None)
        self.on_url = on_url or (lambda _url: None)
        self.url = None
        self._process = None
        self._thread = None

    @property
    def running(self):
        return self._process is not None and self._process.poll() is None

    def start(self, port=DEFAULT_PORT):
        if self.running:
            return
        binary = find_cloudflared()
        if binary is None:
            raise RuntimeError(
                "cloudflared is not installed. Install it once with:\n"
                "    winget install --id Cloudflare.cloudflared\n"
                "then restart this app.")
        self.url = None
        creation = 0
        if os.name == "nt":
            # Otherwise every launch flashes up a console window, which is the
            # thing this whole tab exists to get rid of.
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            [binary, "tunnel", "--url", "http://127.0.0.1:{}".format(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1, creationflags=creation)
        self._thread = threading.Thread(target=self._watch, name="wom-tunnel",
                                        daemon=True)
        self._thread.start()
        self.on_event("opening a tunnel...")

    def _watch(self):
        """Read cloudflared's banner until the address it minted appears."""
        process = self._process
        try:
            for line in process.stdout:
                if self.url is None:
                    match = TUNNEL_URL.search(line)
                    if match:
                        self.url = match.group(0)
                        self.on_url(self.url)
                        self.on_event("tunnel open")
                if "failed" in line.lower() or "error" in line.lower():
                    log.debug("cloudflared: %s", line.strip())
        except Exception:                            # pragma: no cover
            log.exception("reading cloudflared output")
        finally:
            if process.poll() is not None and process is self._process:
                self.on_event("tunnel closed")

    def stop(self):
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        self._process = None
        self.url = None
        self.on_event("tunnel closed")
