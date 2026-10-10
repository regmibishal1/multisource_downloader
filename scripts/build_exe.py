"""Build the Windows app: a folder with MultisourceDownloader.exe, zipped for a release.

This replaces the old portable YouTube app (brief F21): the same main window,
which opens in the simple view the first time. Needs PyInstaller in the
environment (it is a build tool, not a runtime dependency):

    python -m pip install pyinstaller
    python scripts/build_exe.py

The result is dist/MultisourceDownloader/ and dist/MultisourceDownloader-<version>-windows.zip.
gallery-dl is not bundled: it is GPL-2.0 and the app only runs it as a separate
program when it is installed.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = 'MultisourceDownloader'


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from multidownloader import __version__
    return __version__


def build(clean: bool = True) -> Path:
    import PyInstaller.__main__

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
    ]
    if clean:
        args.append('--clean')
    PyInstaller.__main__.run(args)
    folder = dist / NAME
    for name in ('LICENSE', 'README.md'):
        shutil.copyfile(ROOT / name, folder / name)
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
