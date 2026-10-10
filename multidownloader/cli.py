"""msdl: the archive tools (brief section 7).

    msdl verify RUN_DIR_OR_PACKAGE [--custody]

verify re-hashes every file and compares it with the run's SHA256SUMS.txt, or
with an export package's BagIt manifests. --custody also checks the custody
log's chain and its anchors. Exit status: 0 valid, 1 not valid, 2 the path is
not a run folder or a package.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import custody, verify as verify_mod


def _print(line: str) -> None:
    try:
        print(line)
    except UnicodeEncodeError:  # a console that cannot show the symbols
        print(line.encode('ascii', 'replace').decode('ascii'))


def cmd_verify(args) -> int:
    path = Path(args.path)
    if not path.exists():
        _print(f'{path} does not exist.')
        return 2
    try:
        report = verify_mod.verify(path, check_custody=args.custody, anchor_path=custody.default_anchor_path(),
                                   record=not args.no_record)
    except ValueError as exc:
        _print(str(exc))
        return 2
    for line in report.lines():
        _print(line)
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='msdl', description='Multisource Downloader archive tools.')
    sub = parser.add_subparsers(dest='command', required=True)
    v = sub.add_parser('verify', help='Check that saved files have not changed',
                       description='Re-hash every file of a run folder (<download folder>/_runs/<run id>) or an '
                                   'export package and compare it with what was recorded.')
    v.add_argument('path', help='A run folder or an export package')
    v.add_argument('--custody', action='store_true', help="Also check the custody log's chain and anchors")
    v.add_argument('--no-record', action='store_true',
                   help="Do not add a 'verified' line to the run's custody log (packages are never changed)")
    v.set_defaults(func=cmd_verify)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
