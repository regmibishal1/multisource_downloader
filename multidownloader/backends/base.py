"""The interface every download backend implements (brief T2).

A backend downloads one link into a folder:

- ``name``: "yt-dlp", "instaloader", "gdown", "gallery-dl".
- ``available()``: (True, '') or (False, a plain message saying what to install).
- ``download(url, out_dir, options)``: writes files under out_dir and nowhere
  else (staging, see runs.py), and returns True, a path, or a dict naming
  the backend that did the work. Expected failures raise an exception whose
  text errors.classify() can explain; programmer errors may raise anything.

Options a backend may read: ``cancel_event`` (threading.Event: stop soon),
``progress`` (callable(downloaded, total, speed)), ``include_title``,
``write_metadata``, plus its own keys. Rules, enforced by
tests/test_backend_rules.py: no UI toolkit is imported by the engine or the
backends, and nothing changes the process's working folder.
"""
from __future__ import annotations

from typing import Dict, Protocol, Tuple, runtime_checkable


@runtime_checkable
class Backend(Protocol):
    name: str

    def available(self) -> Tuple[bool, str]:
        ...

    def download(self, url: str, out_dir: str, options: Dict) -> object:
        ...


def require(backend: Backend) -> None:
    """Raise RuntimeError with the backend's own message when it cannot run."""
    ok, reason = backend.available()
    if not ok:
        raise RuntimeError(reason)


__all__ = ['Backend', 'require']
