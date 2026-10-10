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

Dependabot (`.github/dependabot.yml`) opens a monthly pull request when a newer version exists for
`pyproject.toml` or the CI workflow's actions. It does not touch `requirements.txt` or
`requirements-lock.txt`, so treat its pull request as a reminder: follow the steps above for that
package in `penv`, push the result to the Dependabot branch (or a new one), and close the Dependabot
pull request if it is replaced. Security alerts and security updates are on separately.

To go back, reinstall from the saved freeze: `.\penv\Scripts\python.exe -m pip install -r penv-before.txt`.

Change log
----------

### 2026-10-10

- Security updates from Dependabot alerts: `urllib3` 2.5.0 to 2.8.0, `requests` 2.32.4 to 2.34.2, `httplib2` 0.31.0 to 0.32.0, `idna` 3.10 to 3.20, `pyasn1` 0.6.1 to 0.6.4, `protobuf` 6.32.1 to 6.33.6 (the Google libraries need protobuf below 7), `soupsieve` 2.8 to 2.9, `filelock` 3.19.1 to 3.32.7 (kept on 3.x), `setuptools` 80.10.2 to 83.0.0, and pip itself to 26.2.1 in `penv`.
- Not updated: `cryptography` stays at 43.0.3 and `pyOpenSSL` at 24.2.1, although alerts ask for 49 and 26. PyDrive2 1.21.3, the newest release, requires `cryptography<44` and `pyOpenSSL<=24.2.1`, so pip cannot install the fixed versions next to it (CI and the Windows app install from pyproject.toml). The app uses them only through PyDrive2 for "Connect with your own Google Cloud project"; downloads use Python's own TLS, not pyOpenSSL.
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
