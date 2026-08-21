"""Shared helpers for the comfyui-siping custom node pack.

ImageWithExtraInfo.py was adapted from another node pack that shipped these
helpers in a sibling ``core`` module. This file re-provides them inside this
package so the relative import ``from .core import ...`` resolves.
"""

import os
import logging
from enum import Enum


class CATEGORY(Enum):
    """Category prefixes concatenated by nodes, e.g. ``MAIN + "/Image"``."""
    MAIN = "siping"
    IMAGE = "/Image"


class TEXTS(Enum):
    """User-facing text constants."""
    FILE_NOT_FOUND = "File not found"


CONFIG = {
    "indent": 2,
}

# ComfyUI boolean widget spec used directly as an input value
BOOLEAN = ("BOOLEAN", {"default": True})

# Custom socket type shared by the load / save metadata nodes
METADATA_RAW = "METADATA_RAW"


class _Logger:
    """Small logger wrapper exposing the methods used by the nodes.

    ``logger.warn`` is called in ImageWithExtraInfo.py; logging's own
    ``Logger.warn`` is a deprecated alias, so forward it to ``warning``.
    """

    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    def debug(self, msg, *args, **kwargs):
        self._log.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self._log.info(msg, *args, **kwargs)

    def warn(self, msg, *args, **kwargs):
        self._log.warning(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._log.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._log.error(msg, *args, **kwargs)


logger = _Logger("comfyui-siping")


def get_size(path) -> str:
    """Return a human-readable file size for the given path."""
    size = float(os.path.getsize(path))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
