"""Write THIRD_PARTY_LICENSES.txt for the packages bundled in the Windows app (brief T19).

It walks the installed dependencies of the app's runtime packages (the same
ones scripts/build_exe.py bundles), and for each one writes its name,
version, license and project link, followed by the license files the package
ships. gallery-dl (GPL-2.0) is not bundled: the app runs it as a separate
program, so it is listed in the notes with a link to its source.

    python scripts/third_party_licenses.py [--out FILE]
"""
from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover  (pip vendors the same module)
    from pip._vendor.packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
# what the Windows app bundles: the core and the instagram, drive and webview extras, not images
BUNDLED = ('yt-dlp[default]', 'instaloader', 'gdown', 'google-auth', 'google-auth-oauthlib', 'pywebview')
NOT_BUNDLED = {'gallery-dl'}
# build tools and installers are never part of the app
BUILD_ONLY = {'pip', 'setuptools', 'wheel', 'pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'pefile',
              'pywin32-ctypes', 'packaging', 'ruff'}
LICENSE_FILE = re.compile(r'(^|/)(licen[cs]e|copying|notice)[^/]*$', re.IGNORECASE)

NOTES = """\
Not included in this app
------------------------

gallery-dl (GPL-2.0-only) is not part of this app. When it is installed, the
app runs it as a separate program for image posts and Bluesky; it never
imports it. Source: https://github.com/mikf/gallery-dl

ffmpeg and Deno are not part of this app either. If you install them, their
own licenses apply (ffmpeg builds are LGPL or GPL depending on the build;
Deno is MIT).

Python and Tcl/Tk
-----------------

The app includes the Python runtime (Python Software Foundation License,
https://docs.python.org/3/license.html) and Tcl/Tk (BSD-style license,
https://www.tcl.tk/software/tcltk/license.html).
"""


def _key(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def _dist(name: str) -> Optional[metadata.Distribution]:
    try:
        return metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None


def closure(roots: Iterable[str]) -> List[metadata.Distribution]:
    """The installed distributions roots need, markers evaluated for this Python, roots included."""
    seen: Dict[str, metadata.Distribution] = {}
    todo: List[Tuple[str, Set[str]]] = []
    for root in roots:
        req = Requirement(root)
        todo.append((req.name, set(req.extras)))
    while todo:
        name, extras = todo.pop()
        key = _key(name)
        if key in NOT_BUNDLED or key in BUILD_ONLY:
            continue
        dist = _dist(name)
        if dist is None:
            continue
        first = key not in seen
        seen[key] = dist
        for line in dist.requires or []:
            req = Requirement(line)
            if req.marker is not None:
                envs = [{'extra': e} for e in extras] or [{'extra': ''}]
                if not any(req.marker.evaluate(env) for env in envs):
                    continue
            if first or req.extras:
                todo.append((req.name, set(req.extras)))
    return sorted(seen.values(), key=lambda d: _key(d.metadata['Name']))


def license_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get('License-Expression')
    if expression:
        return expression
    classifiers = [c.split('::')[-1].strip() for c in meta.get_all('Classifier') or [] if c.startswith('License ::')]
    text = (meta.get('License') or '').strip()
    if classifiers:
        return ', '.join(classifiers)
    if text and len(text) < 80 and '\n' not in text:
        return text
    for _name, body in license_texts(dist):
        first = body.strip().splitlines()[0].strip() if body.strip() else ''
        if re.fullmatch(r'(The )?[\w.\- ]{2,40} Licen[cs]e( 2\.0| \(.+\))?', first):
            return first  # "MIT License", "Apache License 2.0"
    return 'see the license text below' if text or license_texts(dist) else 'unknown'


def project_url(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    urls = meta.get_all('Project-URL') or []
    for wanted in ('source', 'repository', 'code', 'github', 'homepage', 'home'):
        for entry in urls:
            label, _, url = entry.partition(',')
            if label.strip().lower().startswith(wanted):
                return url.strip()
    return meta.get('Home-page') or (urls[0].partition(',')[2].strip() if urls else '')


def license_texts(dist: metadata.Distribution) -> List[Tuple[str, str]]:
    texts = []
    for entry in dist.files or []:
        name = str(entry).replace('\\', '/')
        if LICENSE_FILE.search(name) and not name.endswith(('.py', '.pyc')):
            try:
                texts.append((name.rsplit('/', 1)[-1], Path(dist.locate_file(entry)).read_text(
                    encoding='utf-8', errors='replace').strip()))
            except OSError:
                continue
    if not texts and len((dist.metadata.get('License') or '').strip()) >= 80:
        texts.append(('License (from the package metadata)', dist.metadata['License'].strip()))
    return texts


def render(dists: Iterable[metadata.Distribution]) -> str:
    from multidownloader import __version__

    dists = list(dists)
    out = [f'Third-party software in Multisource Downloader {__version__}',
           '=' * 60, '',
           'Multisource Downloader itself is MIT licensed (see LICENSE). It includes the',
           f'following {len(dists)} packages, each under its own license.', '']
    for dist in dists:
        out.append(f'- {dist.metadata["Name"]} {dist.version}: {license_of(dist)}  {project_url(dist)}'.rstrip())
    out += ['', NOTES]
    for dist in dists:
        out += ['', '-' * 60, f'{dist.metadata["Name"]} {dist.version}', f'License: {license_of(dist)}']
        url = project_url(dist)
        if url:
            out.append(f'Project: {url}')
        for filename, text in license_texts(dist):
            out += ['', f'[{filename}]', text]
    return '\n'.join(out).rstrip() + '\n'


def write(target: Path) -> Path:
    target.write_text(render(closure(BUNDLED)), encoding='utf-8')
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', default=str(ROOT / 'THIRD_PARTY_LICENSES.txt'), help='Where to write the file')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    print(f'Wrote {write(Path(args.out))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
