"""Keep the computer from sleeping during a run (brief T21).

Laptops going to sleep is the most common reason long batches stop. The
display may still turn off; only system sleep is held off, and only while
a run is going.

- Windows: SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED). It
  applies to the calling thread, so use keep_awake() on the thread that runs
  the downloads.
- macOS: ``caffeinate -i -w <pid>`` (it also ends if this process dies).
- Linux: ``systemd-inhibit --what=sleep`` around a waiting child, when
  available.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
from typing import Iterator, Optional

_log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
WHY = 'multisource-downloader is saving files'


def _windows_hold(on: bool) -> bool:
    import ctypes

    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


def _start_inhibitor() -> Optional[subprocess.Popen]:
    if sys.platform == 'darwin' and shutil.which('caffeinate'):
        return subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform.startswith('linux') and shutil.which('systemd-inhibit'):
        return subprocess.Popen(['systemd-inhibit', '--what=sleep', f'--why={WHY}', '--who=multisource-downloader',
                                 'sleep', 'infinity'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return None


@contextlib.contextmanager
def keep_awake(enabled: bool = True) -> Iterator[bool]:
    """Hold off system sleep inside the block; yields whether it could."""
    if not enabled:
        yield False
        return
    held = False
    proc = None
    try:
        if sys.platform == 'win32':
            held = _windows_hold(True)
        else:
            proc = _start_inhibitor()
            held = proc is not None
    except Exception as exc:  # never let this stop a run
        _log.warning('Could not keep the computer awake: %s', exc)
    if not held:
        _log.info('Running without a sleep block (not available on this system)')
    try:
        yield held
    finally:
        try:
            if sys.platform == 'win32' and held:
                _windows_hold(False)
            if proc is not None:
                proc.terminate()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=5)
        except Exception as exc:
            _log.warning('Could not release the sleep block: %s', exc)


__all__ = ['keep_awake']
