"""Write requirements-lock.txt: the exact package versions the tests passed with.

Run it with the environment you tested in, after every dependency change:

    .\\penv\\Scripts\\python.exe scripts/snapshot_env.py

It records every installed distribution (pip freeze style), the Python
version, and the versions of the external programs the app uses (ffmpeg,
Deno). Packages installed from a local path are left out, and nothing that
identifies the machine or the user (paths, user names) is written, because
the repository is public. requirements.txt must agree with it; the test
tests/test_dependencies.py checks that.
"""
from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'requirements-lock.txt'
SELF = 'multisource-downloader'


def _canonical(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def _is_local(dist) -> bool:
    try:
        direct = dist.read_text('direct_url.json')
    except FileNotFoundError:
        direct = None
    return bool(direct and '"url": "file:' in direct)


def installed() -> List[str]:
    pins = {}
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        if not name or _canonical(name) == SELF or _is_local(dist):
            continue
        pins[_canonical(name)] = f'{name}=={dist.version}'
    return [pins[key] for key in sorted(pins)]


def tool_version(cmd: str, args: List[str], pattern: str) -> Optional[str]:
    path = shutil.which(cmd)
    if not path:
        return None
    try:
        out = subprocess.run([path, *args], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(pattern, out)
    return match.group(1) if match else None


def render() -> str:
    ffmpeg = tool_version('ffmpeg', ['-version'], r'ffmpeg version (\S+)')
    deno = tool_version('deno', ['--version'], r'deno (\S+)')
    header = [
        '# Exact versions the test suite passed with. Regenerate after any dependency change:',
        '#   python scripts/snapshot_env.py',
        f'# Snapshot taken: {datetime.now(timezone.utc).strftime("%Y-%m-%d")} (UTC)',
        f'# Python: {platform.python_version()} ({platform.system()} {platform.machine()})',
        f'# ffmpeg: {ffmpeg or "not found"}',
        f'# Deno: {deno or "not found"}',
        '# requirements.txt is the subset the app needs at run time; the rest here are build',
        '# and test tools. DEPENDENCIES.md explains each change.',
        '',
    ]
    return '\n'.join(header + installed()) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true',
                        help='exit 1 if requirements-lock.txt differs from this environment (ignores the date line)')
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        def body(t):
            return [line for line in t.splitlines() if not line.startswith('# Snapshot taken')]
        current = LOCK.read_text(encoding='utf-8') if LOCK.exists() else ''
        if body(current) != body(text):
            print('requirements-lock.txt is out of date: run python scripts/snapshot_env.py', file=sys.stderr)
            return 1
        print('requirements-lock.txt matches this environment.')
        return 0
    LOCK.write_text(text, encoding='utf-8', newline='\n')
    print(f'Wrote {LOCK.name}: {len(installed())} packages.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
