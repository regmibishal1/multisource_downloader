"""--self-test: check a build works end to end without the network or the user's data (brief Q2).

It opens the main window (hidden), runs one fake download through the real
run, scheduler and manifest code, saves and reads back a fake sign-in, and
checks the bundled components import. Everything is written to a temporary
folder that is removed afterwards; the user's settings, sign-ins, history and
logs are never touched. Exit status 0 means every step passed. The windowed
Windows app has no console, so --report FILE writes the step list to a file.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, List, Optional

FAKE_URL = 'https://x.com/selftest/status/1'


class _FakeDownloader:
    """Writes one small file, as a backend would, into the job's staging folder."""

    def download(self, source, url, options, out_dir=None):
        (Path(out_dir) / '2026-01-01_1.jpg').write_bytes(b'self-test ' + url.encode())
        return True


_ENV = ('LOCALAPPDATA', 'APPDATA', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME')


def _isolate(tmp: Path) -> Callable[[], None]:
    """Point every per-user folder into tmp; returns a function that puts them back."""
    from .. import session_store
    saved_env = {name: os.environ.get(name) for name in _ENV}
    saved_root = session_store.SESSION_ROOT
    data = tmp / 'data'
    for name in _ENV:
        os.environ[name] = str(data)
    session_store.SESSION_ROOT = data / 'multisource-downloader' / 'sessions'

    def restore():
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        session_store.SESSION_ROOT = saved_root

    return restore


OPTIONAL = (('instaloader', 'Instaloader'), ('gdown', 'gdown'), ('google_auth_oauthlib', 'Google sign-in'), ('webview', 'pywebview'))


def expected_components() -> List[str]:
    """In the Windows app: the optional parts scripts/build_exe.py bundled (components.txt next to the exe)."""
    if not getattr(sys, 'frozen', False):
        return []
    try:
        text = (Path(sys.executable).parent / 'components.txt').read_text(encoding='utf-8')
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _components() -> str:
    # without a console sys.stdout/stderr are None and any library that prints fails (gdown did)
    if sys.stdout is None or sys.stderr is None:
        raise RuntimeError('there is nowhere to write output: libraries that print would fail')
    import yt_dlp
    found = [f'yt-dlp {yt_dlp.version.__version__}']
    try:
        import yt_dlp_ejs  # noqa: F401  (YouTube needs it)
        found.append('yt-dlp-ejs')
    except ImportError:
        raise RuntimeError('yt-dlp-ejs is missing, so YouTube would fail') from None
    expected = expected_components()
    missing = []
    for name, label in OPTIONAL:
        try:
            __import__(name)
            found.append(label)
        except Exception as exc:  # not bundled, or bundled without a file it needs
            if name in expected:
                missing.append(f'{label} ({type(exc).__name__}: {exc})')
            else:
                found.append(f'{label} not included')
    if missing:
        raise RuntimeError('bundled but cannot load: ' + '; '.join(missing))
    if getattr(sys, 'frozen', False):
        # gallery-dl (GPL-2.0) is never part of the Windows app, not even its package metadata
        import importlib.util
        from importlib import metadata
        try:
            metadata.version('gallery-dl')
            raise RuntimeError("gallery-dl's package metadata is in the app; remove it from the build")
        except metadata.PackageNotFoundError:
            pass
        if importlib.util.find_spec('gallery_dl') is not None:
            raise RuntimeError('gallery-dl is bundled; it must only run as a separate program')
    from .help import HELP_FILE
    if not HELP_FILE.is_file():
        raise RuntimeError('the help page is missing')
    found.append('help page')
    return ', '.join(found)


def _window(ui_class) -> str:
    import tkinter as tk
    from .. import settings
    from . import theme

    root = tk.Tk()
    root.withdraw()  # never shown and never takes the focus
    try:
        mode = theme.apply(root, 'system')
        app = ui_class(root, offer_resume=False, prefs=settings.defaults(), view='simple')
        app.set_view('full')
        root.update_idletasks()
        import logging
        logging.getLogger().removeHandler(app._log_handler)
    finally:
        root.destroy()
    return f'{mode} theme'


def _fake_run(tmp: Path) -> str:
    from ..history import History
    from ..runner import Job, Runner
    from ..runs import Run, read_manifest

    anchors = tmp / 'anchors.log'
    run = Run(tmp / 'out', history=History(tmp / 'history.sqlite'), anchor_path=anchors)
    try:
        results = Runner(run, _FakeDownloader(), delays={}, default_delay=(0, 0)).run([Job(FAKE_URL, 'Twitter')])
    finally:
        run.finish()
    if [r.status for r in results] != ['saved']:
        raise RuntimeError(f'the fake link was not saved: {[(r.status, r.error_message) for r in results]}')
    rows = read_manifest(run.dir / 'manifest.jsonl')
    sums = (run.dir / 'SHA256SUMS.txt').read_text(encoding='utf-8')
    if len(rows) != 1 or not (run.dir / 'manifest.csv').exists() or '2026-01-01_1.jpg' not in sums:
        raise RuntimeError('the run folder is incomplete')
    from .. import verify
    report = verify.verify(run.dir, check_custody=True, anchor_path=anchors)
    if not report.ok:
        raise RuntimeError('verify failed: ' + ' '.join(report.lines()))
    return f'{len(results[0].files)} file, manifest, SHA256SUMS and custody log written and verified'


def _fake_signin(tmp: Path) -> str:
    from .. import session_store, signins
    from ..platforms import BY_KEY

    platform = BY_KEY['x']
    # never save or remove a sign-in outside the temporary folder (checked before anything is created)
    if tmp.resolve() not in Path(session_store.SESSION_ROOT).resolve().parents:
        raise RuntimeError('the sign-in folder is not the temporary one; nothing was changed')
    expires = int(time.time()) + 30 * 24 * 3600
    text = ('# Netscape HTTP Cookie File\n'
            f'.x.com\tTRUE\t/\tTRUE\t{expires}\tauth_token\tSELFTEST\n'
            f'.x.com\tTRUE\t/\tTRUE\t{expires}\ttwid\tu%3D1\n')
    signins.import_cookies_text(platform, text, terms_version=platform.terms.version if platform.terms else None)
    status = signins.status(platform)
    if not status.signed_in or [c.value for c in signins.read_cookies(platform) if c.name == 'auth_token'] != [
            'SELFTEST']:
        raise RuntimeError(f'the sign-in did not read back ({status.state})')
    signins.remove(platform)
    if signins.status(platform).signed_in:
        raise RuntimeError('the sign-in was not removed')
    return 'saved, read back and removed'


def _signin_window_starts() -> str:
    """The Windows app runs "Sign in here" as a second copy of itself; --help opens no window."""
    import subprocess
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    done = subprocess.run([sys.executable, '--signin-window', '--help'], capture_output=True, text=True,
                          timeout=120, creationflags=flags)
    if done.returncode != 0 or '--platform' not in done.stdout:
        raise RuntimeError(f'the sign-in window did not start (exit {done.returncode})')
    return 'starts'


def run(ui_class=None, report: Optional[str] = None) -> int:
    tmp = Path(tempfile.mkdtemp(prefix='msdl-selftest-'))
    lines: List[str] = []
    failed = False

    def step(name: str, fn: Callable[[], str]) -> None:
        nonlocal failed
        try:
            detail = fn()
            lines.append(f'ok    {name}: {detail}')
        except Exception as exc:
            failed = True
            lines.append(f'FAIL  {name}: {exc}')
            lines.extend('      ' + line for line in traceback.format_exc().rstrip().splitlines())

    restore = _isolate(tmp)
    try:
        from .. import __version__
        lines.append(f'multisource-downloader {__version__} self-test')
        step('components', _components)
        if ui_class is not None:
            step('window', lambda: _window(ui_class))
        step('download', lambda: _fake_run(tmp))
        step('sign-in', lambda: _fake_signin(tmp))
        if getattr(sys, 'frozen', False):
            step('sign-in window', _signin_window_starts)
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)
    lines.append('self-test: ' + ('FAILED' if failed else 'ok'))
    text = '\n'.join(lines) + '\n'
    if report:
        Path(report).write_text(text, encoding='utf-8')
    try:
        print(text, end='')
    except Exception:  # the windowed app has no console
        pass
    return 1 if failed else 0


__all__ = ['run']
