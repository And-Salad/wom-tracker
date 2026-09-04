"""Where each entry point writes its log."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import data_dir, log_path_for


def setup_logging(verbose=False, role="app"):
    """Start logging for one entry point.

    `role` picks the file: the server writes wom-web.log and CLI jobs write
    wom-cli.log. They can run at the same time, and on Windows a rotation
    cannot rename a file another process is holding open.
    """
    os.makedirs(data_dir(), exist_ok=True)
    handler = RotatingFileHandler(log_path_for(role), maxBytes=512_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        root.addHandler(console)
