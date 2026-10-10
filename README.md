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
- Select any supported source from the dropdown. Google Drive links try public access first and use your Drive sign-in only when Drive refuses the link.
- Public posts download without signing in. When a download needs a sign-in, the app offers to set one up after the run, and **Sign-ins** (top right) shows every platform's status.

Sign-ins
--------

Most platforms' terms do not allow automated downloading. This app is meant for preserving public-interest evidence and your own content. You are responsible for how you use it. If you plan to use files in legal proceedings, ask a lawyer.

Before the first sign-in for a platform, the app shows what that platform's terms say and asks you to confirm. Facebook allows one account per person, in your real name, so do not create another Facebook account for this app. On other platforms, if you sign in at all, use an account you can afford to lose.

Ways to sign in, per platform:
- **Sign in here** (Instagram, X, Facebook, TikTok, Threads, Reddit): opens the platform's own sign-in page in a small window. Sign in as usual, including any security code; the window closes by itself. It starts from an empty private profile every time, and your password goes only to the platform. Your password manager may not fill this window, but paste works. Needs the `webview` extra (`pip install ".[webview]"`, included in `all`) and, on Windows, Microsoft's WebView2 Runtime, which Windows 11 already has. Not offered for YouTube and Google Drive, because Google blocks signing in inside other apps.
- **Use my Firefox sign-in**: the app reads the platform's cookies from Firefox, where you are already signed in. Nothing is exported, and the app re-reads Firefox at the start of every run. Chrome, Edge and other Chromium browsers lock their sign-ins on Windows, so use Firefox or a cookies file there.
- **Import a cookies file**: choose or paste a `cookies.txt` exported with the "Get cookies.txt LOCALLY" extension (Chrome, Edge) or "cookies.txt" (Firefox). Do not use the extension named just "Get cookies.txt". The app keeps only the platform's own cookies, so you can delete the original export afterwards.
- **Username and password** (Instagram only, under "More ways to sign in"): not recommended, because Instagram is more likely to lock accounts that sign in from scripts.

From the command line:

```powershell
.\penv\Scripts\python.exe -m multidownloader.signin_cli status
.\penv\Scripts\python.exe -m multidownloader.signin_cli import instagram --browser firefox
.\penv\Scripts\python.exe -m multidownloader.signin_cli import x --cookies C:\path\to\cookies.txt
.\penv\Scripts\python.exe -m multidownloader.signin_cli remove --all
```

`import` shows the terms notice and asks you to type `yes`; pass `--accept-terms` when running without a terminal.

Google Drive: public links need nothing. For files shared with your Google account, use your Firefox sign-in or a google.com cookies file (needs gdown 6 or newer). The advanced alternative, **Connect with your own Google Cloud project**, walks through creating a free Desktop OAuth client and signs in through your normal browser. Google signs you out every 7 days while that project is in Testing status, and the app tells you when that happens. A `client_secrets.json` left in the project root by older versions is copied into `multidownloader/.sessions/googledrive/`.

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

All sign-ins live under `multidownloader/.sessions/` (already ignored by git), one folder per platform (`instagram/`, `twitter/`, `youtube/`, `googledrive/`, ...):
- `cookies.txt`: the platform's cookies only, in Netscape format. yt-dlp and Instaloader both read it.
- `signin.json`: how you signed in, the account id, when the sign-in expires, and which version of the platform's terms notice you accepted.
- `instagram/*.session` and `googledrive/credentials.json`: Instaloader sessions and PyDrive2 tokens.

Older versions created `cookies.txt` automatically after any download. A file without a sign-in in it is now ignored, so you do not need to delete it. **Remove** in Sign-ins deletes a platform's files; **Remove all sign-ins** deletes them for every platform (your `client_secrets.json` is kept).

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
