"""Asking the user for sign-in details without the engine knowing the UI (brief T6).

Backends never open dialogs. When a sign-in flow needs a username, password
or two-factor code, it calls an AuthProvider:

- the window's sign-in dialogs answer on the Tk thread;
- TerminalAuth asks in the terminal with getpass, and only when there is one;
- NoAuth answers nothing, so the flow fails with a sign-in error that names
  the command to run instead.

Passwords pass straight to the platform's own library and are never stored.
"""
from __future__ import annotations

import getpass
import sys
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable


@dataclass
class Credentials:
    username: str
    password: str


@runtime_checkable
class AuthProvider(Protocol):
    def request_credentials(self, platform: str, reason: str) -> Optional[Credentials]:
        ...

    def request_2fa_code(self, platform: str) -> Optional[str]:
        ...


class NoAuth:
    """For runs without anyone to ask (scheduled jobs, CI)."""

    def request_credentials(self, platform: str, reason: str) -> Optional[Credentials]:
        return None

    def request_2fa_code(self, platform: str) -> Optional[str]:
        return None


class TerminalAuth:
    """Asks in the terminal; answers nothing when stdin is not a terminal."""

    def __init__(self, *, stdin=None, prompt: Callable[[str], str] = input,
                 secret: Callable[[str], str] = getpass.getpass, out=None):
        self.stdin = stdin or sys.stdin
        self.prompt = prompt
        self.secret = secret
        self.out = out or sys.stderr

    def _interactive(self) -> bool:
        try:
            return bool(self.stdin and self.stdin.isatty())
        except (AttributeError, ValueError):
            return False

    def request_credentials(self, platform: str, reason: str) -> Optional[Credentials]:
        if not self._interactive():
            return None
        print(reason, file=self.out)
        try:
            username = self.prompt(f'{platform} username: ').strip()
            password = self.secret(f'{platform} password (not shown, not saved): ')
        except (EOFError, KeyboardInterrupt):
            return None
        if not username or not password:
            return None
        return Credentials(username, password)

    def request_2fa_code(self, platform: str) -> Optional[str]:
        if not self._interactive():
            return None
        try:
            code = self.prompt(f'{platform} security code (from your authenticator app or text message): ')
        except (EOFError, KeyboardInterrupt):
            return None
        code = ''.join(ch for ch in code if ch.isdigit())
        return code or None


__all__ = ['AuthProvider', 'Credentials', 'NoAuth', 'TerminalAuth']
