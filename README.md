Multi-source Downloader
======================

This project provides a Tkinter UI and a reusable `multidownloader` package for downloading media from multiple platforms. Handlers are modular so they can be used programmatically or from the command line.

Supported sources (install only the extras you need):
- Google Drive: public downloads via `gdown`, authenticated via `PyDrive2`
- Instagram: `instaloader` (supports saved sessions and 2FA)
- TikTok: `yt-dlp`
- Threads: `yt-dlp`
- Twitter / X: `yt-dlp`
- Reddit: `yt-dlp`
- Facebook: `yt-dlp`
- YouTube: `yt-dlp`

Portable Standalone Version
---------------------------

Looking for a simple, no-installation-required YouTube downloader? Check out the **[Portable Manager](./portable_manager/)** - a standalone `.exe` that anyone can download and run!

Quick start
-----------

1. Create and activate the project virtual environment (`penv`). Always run commands in this README through `penv\Scripts\python.exe`.

    ```powershell
    python -m venv .\penv
    .\penv\Scripts\activate
    ```

2. Install the packages. `requirements.txt` pins exact versions for `penv`. Running it again in an existing `penv` upgrades yt-dlp:

    ```powershell
    .\penv\Scripts\python.exe -m pip install -r requirements.txt
    ```

    Or install the project as a package with only the platforms you need (`instagram`, `drive`, or `all`). This works on Windows, macOS and Linux, and adds the `multisource-downloader` (window) and `multidownloader-batch` (CLI) commands:

    ```powershell
    python -m pip install ".[all]"
    ```

    YouTube downloads also need [Deno](https://docs.deno.com/runtime/getting_started/installation/) installed. Without it yt-dlp cannot solve YouTube's JavaScript challenges, and some formats are missing.

3. Launch the UI:

    ```powershell
    .\penv\Scripts\python.exe multisource_downloader_ui.py
    ```

UI notes
--------

- Files are saved to `Downloads\Multisource` in your home folder unless you choose another folder.
- Select any supported source from the dropdown. Additional options appear when a source needs them (e.g., Google Drive public vs authenticated, Instagram auth mode, cookie import button for yt-dlp sources).
- Instagram "Auto" mode silently reuses the cached session in `multidownloader/.sessions/instagram/`. Use "Authenticated (prompt now)" the first time to sign in and save the session.
- For yt-dlp backed platforms (TikTok, Threads, Twitter/X, Reddit, Facebook, YouTube) you can import a browser `cookies.txt` once; the UI copies it into `multidownloader/.sessions/<source>/cookies.txt` for reuse. Downloads only use cookies once you have imported a file. Older versions created this file automatically after any download, so if you never imported cookies but the file exists, delete it.
- Google Drive authenticated downloads reuse cached PyDrive2 credentials stored in `multidownloader/.sessions/googledrive/credentials.json`. Keep your `client_secrets.json` in the project root or copy it into `multidownloader/.sessions/googledrive/` so the handler can locate it during the auth flow.

Batch CLI (loot_report_scraper integration)
------------------------------------------

The package exposes a CLI that can read the scraper output (`grouped_by_source.json` or CSV) and queue downloads automatically. Run it from the multisource_downloader project root via the `penv` interpreter:

```powershell
# From the multisource_downloader project root
.\penv\Scripts\python.exe -m multidownloader.batch path\to\grouped_by_source.json --out-dir path\to\downloads
```

Options:
- `--limit` / `--per-source-limit` restrict how many links are attempted.
- `--dry-run` prints the plan without downloading.
- Unsupported hosts are reported in the summary so you can triage manually.
- Exit codes: `0` everything attempted succeeded, `1` some downloads failed, `2` nothing was attempted because every link was from an unsupported site.

Sessions and credential storage
-------------------------------

All reusable auth artifacts live under `multidownloader/.sessions/` (already ignored by git). Folder names are the source name in lower case, with spaces and other symbols replaced by `_`:
- `instagram/`: saved Instaloader `.session` files and metadata.
- `googledrive/credentials.json`: PyDrive2 credential cache.
- `<source>/cookies.txt` (for example `twitter/`, `youtube/`): browser-exported cookies for yt-dlp handlers.

The helpers in `multidownloader.session_store` read/write these files and are used by the handlers automatically.

Testing
-------

Always run test suites with the project environment (`penv`):

```powershell
.\penv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Integration with loot_report_scraper
------------------------------------

The scraper now ships a helper script (`run_downloader.py`) that shells into this project. Set the environment variables:

```powershell
$env:MULTIDOWNLOADER_PYTHON = "C:\path\to\multisource_downloader\penv\Scripts\python.exe"
$env:MULTIDOWNLOADER_ROOT = "C:\path\to\multisource_downloader"
```

Then from the scraper project run:

```powershell
.\penv\Scripts\python.exe run_downloader.py --limit 20
```

The bridge script invokes `multidownloader.batch` with the latest `grouped_by_source.json` and writes downloads alongside the scraper results.

Security reminders
------------------

- Session files and cookies grant authenticated access. Keep `.sessions/` private and never commit its contents.
- Rotate cookies or rerun authentication if downloads start failing with auth errors.
