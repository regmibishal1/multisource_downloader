"""The "Sign in here" window (brief 0.4, M1).

Runs as its own process because pywebview and Tk each need the main thread:

    python -m multidownloader.gui.signin_window --platform instagram --out FILE

It opens the platform's own sign-in page in an empty private web profile, so
nothing from other sites or earlier sessions is shared and nothing stays in
a web cache afterwards. The user signs in as usual (the password goes only
to the site). When the platform's sign-in cookie appears, the window waits
2 s for the rest, writes the cookies and the window's User-Agent to FILE as
JSON, and closes.

Progress goes to stdout as JSON lines for the parent window:
    {"event": "url", "url": "..."}     the page now shown
    {"event": "done"}                  cookies written to FILE
    {"event": "timeout"}               no sign-in after the time limit
    {"event": "closed"}                the user closed the window
    {"event": "error", "message": "..."}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List

from ..platforms import BY_KEY
from ..signins import cookies_from_webview, find_markers

POLL_SECONDS = 1.0
SETTLE_SECONDS = 2.0
TIMEOUT_SECONDS = 600
WIDTH, HEIGHT = 480, 720


def emit(event: str, **fields) -> None:
    print(json.dumps({'event': event, **fields}), flush=True)


def morsels_to_dicts(cookies) -> List[Dict]:
    """pywebview returns a list of http.cookies.SimpleCookie, one cookie each."""
    found = []
    for simple in cookies or []:
        for name, morsel in simple.items():
            found.append({
                'name': name,
                'value': morsel.value,
                'domain': morsel['domain'],
                'path': morsel['path'] or '/',
                'expires': morsel['expires'],
                'secure': bool(morsel['secure']),
                'httponly': bool(morsel['httponly']),
            })
    return found


def watch(window, platform, out_path: str, timeout: float, state: dict) -> None:
    """Poll the window until the sign-in cookie appears or time runs out."""
    deadline = time.time() + timeout
    last_url = None
    more_path = out_path + '.more'  # the app touches this file when the user selects Keep waiting
    seen_more = None
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            stamp = os.path.getmtime(more_path)
        except OSError:
            stamp = None
        if stamp is not None and stamp != seen_more:
            seen_more = stamp
            deadline = time.time() + timeout
            emit('extended')
        try:
            url = window.get_current_url()
            if url and url != last_url:
                last_url = url
                emit('url', url=url)
            cookies = morsels_to_dicts(window.get_cookies())
        except Exception:  # window closing, page mid-navigation
            if state.get('closed'):
                return
            continue
        if not find_markers(platform, cookies_from_webview(cookies))[0]:
            continue
        time.sleep(SETTLE_SECONDS)  # let the site set its remaining cookies
        try:
            cookies = morsels_to_dicts(window.get_cookies()) or cookies
            user_agent = window.evaluate_js('navigator.userAgent')
        except Exception:
            user_agent = None
        with open(out_path, 'w', encoding='utf-8') as fp:  # parent created it with 0600
            json.dump({'cookies': cookies, 'user_agent': user_agent}, fp)
        state['done'] = True
        emit('done')
        window.destroy()
        return
    state['timed_out'] = True
    emit('timeout')
    window.destroy()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--platform', required=True, choices=sorted(k for k, p in BY_KEY.items() if p.web_signin_url))
    parser.add_argument('--out', required=True)
    parser.add_argument('--x', type=int)
    parser.add_argument('--y', type=int)
    parser.add_argument('--timeout', type=float, default=TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    platform = BY_KEY[args.platform]

    try:
        import webview
    except ImportError:
        emit('error', message='pywebview is not installed.')
        return 2

    state: dict = {}
    window = webview.create_window(
        f'Sign in to {platform.name}', platform.web_signin_url,
        width=WIDTH, height=HEIGHT, x=args.x, y=args.y, min_size=(400, 560),
    )
    window.events.closed += lambda: state.__setitem__('closed', True)
    try:
        # private_mode: a fresh, empty profile every time, deleted afterwards
        webview.start(watch, (window, platform, args.out, args.timeout, state), private_mode=True)
    except Exception as exc:
        emit('error', message=str(exc))
        return 1
    if not state.get('done') and not state.get('timed_out'):
        emit('closed')
    return 0 if state.get('done') else 1


if __name__ == '__main__':
    sys.exit(main())
