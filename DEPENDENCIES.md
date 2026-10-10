Dependencies
============

Three files say which versions the app uses:

- `pyproject.toml`: the lowest versions the app works with, for `pip install .` (with extras: `instagram`, `drive`, `webview`, `images`, `all`).
- `requirements.txt`: the exact versions of what the app needs at run time, installed into `penv`.
- `requirements-lock.txt`: a snapshot of every package (run time, build and test tools) the test suite last passed with, plus the Python, ffmpeg and Deno versions.

`tests/test_dependencies.py` checks that the three agree.

Updating a dependency
---------------------

1. Save the current state: `.\penv\Scripts\python.exe -m pip freeze > penv-before.txt` (keep it outside the repository).
2. Install the new version: `.\penv\Scripts\python.exe -m pip install "<package>==<version>"`.
3. Update its pin in `requirements.txt` (and the lower bound in `pyproject.toml` if the app now needs the newer version).
4. Run the tests: `.\penv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`.
5. Take a new snapshot: `.\penv\Scripts\python.exe scripts/snapshot_env.py`.
6. Add a line to the log below.
7. Before pushing: `python scripts/check_public.py --commits origin/master..HEAD`.

To go back, reinstall from the saved freeze: `.\penv\Scripts\python.exe -m pip install -r penv-before.txt`.

Change log
----------

### 2026-10-10

- Added the rest of yt-dlp's `default` extra to `penv`: `brotli` 1.2.0, `mutagen` 1.48.1, `pycryptodomex` 3.24.0 and `websockets` 17.2 (yt-dlp itself unchanged at 2026.8.19). Some sites need websockets (live streams) or pycryptodomex (encrypted streams); `pip install ".[all]"` and the Windows app already had them.
- Added `gallery-dl` 1.32.16 for image posts (photo-only X posts, carousels, Reddit, TikTok and Facebook photos) and Bluesky. It is GPL-2.0-only, so the app runs it as a separate program and never imports it.

### 2026-10-09

- `gdown` 5.2.1 to 6.4.2: version 6 accepts a cookies file, which Google Drive sign-in needs. It also dropped the `fuzzy=` argument; the app passes it only to versions that still have it, so 5.x keeps working for public links.
- Added `pywebview` 6.2.1 for "Sign in here", with `bottle` 0.13.4 and `proxy_tools` 0.1.0, and on Windows `pythonnet` 3.2.1 and `clr_loader` 0.3.1. On Windows it uses Microsoft's WebView2 Runtime, which Windows 11 includes.
- `yt-dlp` 2026.2.4 to 2026.8.19, and added `yt-dlp-ejs` 0.8.0: since 2025.11.12, yt-dlp needs a JavaScript runtime and this component to read YouTube.
- Removed from `requirements.txt`: PyInstaller and the packages only it needs (`pyinstaller-hooks-contrib`, `altgraph`, `pefile`, `pywin32-ctypes`), which are for building the Windows app (`scripts/build_exe.py`); and `greenlet` and `decorator`, which nothing uses. They stay installed in `penv`.

External programs
-----------------

- **Deno** 2.9.7 (`winget install DenoLand.Deno`): the JavaScript runtime yt-dlp needs for YouTube.
- **ffmpeg** 9.0.2 (`winget install Gyan.FFmpeg`): merges the best video and audio into one file. Without it, the two streams are saved separately.
- **Firefox** (`winget install Mozilla.Firefox`): only for "Use my Firefox sign-in"; not needed otherwise.
