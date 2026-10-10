"""Build the Windows app: a folder with MultisourceDownloader.exe, zipped for a release.

This replaces the old portable YouTube app (brief F21): the same main window,
which opens in the simple view the first time. Needs PyInstaller in the
environment (it is a build tool, not a runtime dependency):

    python -m pip install pyinstaller
    python scripts/build_exe.py

The result is dist/MultisourceDownloader/ and dist/MultisourceDownloader-<version>-windows.zip,
with LICENSE, README.md and THIRD_PARTY_LICENSES.txt next to the exe. gallery-dl
is not bundled: it is GPL-2.0 and the app only runs it as a separate program
when it is installed. Check a build with:

    dist\MultisourceDownloader\MultisourceDownloader.exe --self-test --report self-test.txt
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = 'MultisourceDownloader'

# optional parts bundled when installed in the build environment: module, PyInstaller options
OPTIONAL = {
    'instaloader': ['--collect-submodules=instaloader', '--copy-metadata=instaloader'],
    # gdown reads its own version from the package metadata when imported
    'gdown': ['--collect-submodules=gdown', '--copy-metadata=gdown'],
    'pydrive2': ['--collect-submodules=pydrive2', '--copy-metadata=PyDrive2'],
    # "Sign in here": pywebview with its WebView2 files, and pythonnet on Windows
    'webview': ['--collect-all=webview', '--copy-metadata=pywebview'],
    'pythonnet': ['--collect-all=pythonnet', '--collect-all=clr_loader'],
}
COMPONENTS_FILE = 'components.txt'


def optional_parts():
    return [name for name in OPTIONAL if importlib.util.find_spec(name) is not None]


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from multidownloader import __version__
    return __version__


def build(clean: bool = True) -> Path:
    import PyInstaller.__main__

    parts = optional_parts()

    dist = ROOT / 'dist'
    work = ROOT / 'build'
    args = [
        str(ROOT / 'multisource_downloader_ui.py'),
        f'--name={NAME}',
        '--windowed',          # no console window
        '--onedir',            # a folder: starts faster than one file and keeps the license files visible
        '--noconfirm',
        f'--distpath={dist}',
        f'--workpath={work}',
        f'--specpath={work}',
        # yt-dlp loads its extractors and the YouTube challenge solver by name
        '--collect-submodules=yt_dlp',
        '--collect-data=yt_dlp_ejs',
        '--collect-submodules=multidownloader',
        # the help page, opened from every window's (?) button
        f'--add-data={ROOT / "multidownloader" / "gui" / "help.html"}{os.pathsep}multidownloader/gui',
        '--exclude-module=gallery_dl',  # GPL-2.0: run as a separate program only, never bundled
        '--copy-metadata=yt-dlp',
    ]
    for name in parts:
        args += OPTIONAL[name]
    if clean:
        args.append('--clean')
    PyInstaller.__main__.run(args)
    folder = dist / NAME
    for name in ('LICENSE', 'README.md'):
        shutil.copyfile(ROOT / name, folder / name)
    # every bundled package's license, next to the exe (brief T19)
    sys.path.insert(0, str(ROOT / 'scripts'))
    import third_party_licenses
    third_party_licenses.write(folder / 'THIRD_PARTY_LICENSES.txt')
    # what --self-test must find in this build
    (folder / COMPONENTS_FILE).write_text('\n'.join(p for p in parts if p != 'pythonnet') + '\n', encoding='utf-8')
    return folder


def zip_folder(folder: Path) -> Path:
    target = folder.parent / f'{NAME}-{version()}-windows.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                archive.write(path, Path(NAME) / path.relative_to(folder))
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--no-clean', action='store_true', help="Reuse PyInstaller's cache from the last build")
    parser.add_argument('--no-zip', action='store_true', help='Only build the folder')
    args = parser.parse_args(argv)
    folder = build(clean=not args.no_clean)
    print(f'Built {folder}')
    if not args.no_zip:
        print(f'Zipped {zip_folder(folder)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
