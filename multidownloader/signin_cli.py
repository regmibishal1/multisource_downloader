"""Sign-ins from the command line.

    multidownloader-signin status
    multidownloader-signin import instagram --browser firefox [--profile NAME]
    multidownloader-signin import x --cookies cookies.txt
    multidownloader-signin remove facebook
    multidownloader-signin remove --all

Exit codes: 0 done, 1 the sign-in could not be saved, 2 bad usage or
terms not accepted.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from typing import Optional, Sequence

import logging

from . import logs, session_store, signins
from .platforms import BY_KEY, PLATFORMS
from .signins import SignInError

ALIASES = {'twitter': 'x', 'drive': 'gdrive', 'google-drive': 'gdrive'}


def _platform(name: str):
    key = ALIASES.get(name.lower(), name.lower())
    if key in BY_KEY:
        return BY_KEY[key]
    guess = difflib.get_close_matches(key, list(BY_KEY) + list(ALIASES), n=1)
    hint = f' Did you mean {ALIASES.get(guess[0], guess[0])}?' if guess else ''
    raise SystemExit(_fail(f'Unknown platform "{name}".{hint}', 2))


def _fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f'{prompt} Type yes to continue: ').strip().lower() == 'yes'
    except EOFError:
        return False


def _terms(platform, accepted: bool) -> Optional[str]:
    """Show the platform's terms notice; return the version once acknowledged."""
    if not platform.terms:
        return None
    if not signins.needs_terms_ack(platform) or accepted:
        return platform.terms.version
    print(f'! {platform.terms.notice}', file=sys.stderr)
    print(f'  {platform.terms.account_advice}', file=sys.stderr)
    print(f"  Read {platform.name}'s terms: {platform.terms.url}", file=sys.stderr)
    if _confirm('I understand and will use this only where I have a right to.'):
        return platform.terms.version
    raise SystemExit(_fail('Not saved. Accept the notice above, or pass --accept-terms when not running '
                           'interactively.', 2))


def cmd_status(_args) -> int:
    width = max(len(p.name) for p in PLATFORMS)
    for st in signins.all_statuses():
        meta = signins.read_meta(st.platform)
        glyph = st.glyph or ' '
        print(f'{st.platform.name:<{width}}  {glyph} {st.text}' + (f'  [{st.platform.key}]' if meta else ''))
    return 0


def cmd_import(args) -> int:
    platform = _platform(args.platform)
    terms = _terms(platform, args.accept_terms)
    try:
        if args.cookies:
            result = signins.import_cookies_file(platform, args.cookies, terms_version=terms, label=args.label)
            print(result.message(), file=sys.stderr)
            print('You can delete the original file now. It contains sign-ins for every site.', file=sys.stderr)
        else:
            profile = None
            if args.browser == 'firefox' and args.profile:
                profiles = {p.name: p for p in signins.firefox_profiles()}
                if args.profile not in profiles:
                    names = ', '.join(profiles) or 'none found'
                    return _fail(f'No Firefox profile named "{args.profile}". Profiles: {names}.', 2)
                profile = profiles[args.profile]
            elif args.browser == 'firefox':
                found = signins.find_firefox_signins(platform)
                if found:
                    profile = found[0].profile
            result = signins.import_from_browser(platform, args.browser, profile, terms_version=terms,
                                                 label=args.label)
            print(result.message(), file=sys.stderr)
    except SignInError as exc:
        return _fail(str(exc), 1)
    print(signins.status(platform).text)
    return 0


def cmd_remove(args) -> int:
    if args.all:
        if not (args.force or _confirm('This deletes saved sessions and cookies for every platform.')):
            return _fail('Not removed. Pass --force when not running interactively.', 2)
        signins.remove_all()
        print('Removed all sign-ins. Downloaded files are not affected.')
        return 0
    if not args.platform:
        return _fail('Name a platform, or pass --all.', 2)
    platform = _platform(args.platform)
    signins.remove(platform)
    print(f'Removed the {platform.name} sign-in. Downloaded files are not affected.')
    return 0


def build_parser() -> argparse.ArgumentParser:
    keys = ', '.join(p.key for p in PLATFORMS)
    parser = argparse.ArgumentParser(
        prog='multidownloader-signin',
        description='Manage the sign-ins downloads use. Sign-ins stay on this computer.',
        epilog=f'Platforms: {keys}. Examples: "import instagram --browser firefox", '
               '"import x --cookies cookies.txt".',
    )
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('status', help='show every platform\'s sign-in status').set_defaults(func=cmd_status)

    imp = sub.add_parser('import', help='save a sign-in from a browser or a cookies.txt file')
    imp.add_argument('platform')
    source = imp.add_mutually_exclusive_group(required=True)
    source.add_argument('--browser', choices=[key for key, _name in signins.BROWSERS])
    source.add_argument('--cookies', metavar='FILE', help='a cookies.txt export')
    imp.add_argument('--profile', help='Firefox profile name (default: the one with a sign-in)')
    imp.add_argument('--label', help='a name for this sign-in, e.g. "Archive account"')
    imp.add_argument('--accept-terms', action='store_true',
                     help="accept the platform's terms notice without a prompt")
    imp.set_defaults(func=cmd_import)

    rem = sub.add_parser('remove', help='delete a saved sign-in')
    rem.add_argument('platform', nargs='?')
    rem.add_argument('--all', action='store_true', help='remove every sign-in')
    rem.add_argument('--force', action='store_true', help='do not ask for confirmation with --all')
    rem.set_defaults(func=cmd_remove)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logs.setup(console=True, console_level=logging.WARNING)
    session_store.migrate_legacy_sessions()
    if not getattr(args, 'func', None):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
