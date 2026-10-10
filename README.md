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
- Bluesky: `gallery-dl` (public posts, no sign-in)
- Image posts (photo-only X posts, Reddit, TikTok and Facebook photo posts): when yt-dlp finds no video, the app gets the images with `gallery-dl`. Image-only Threads posts are not supported yet.

`gallery-dl` is licensed GPL-2.0. The app does not include or import it: it runs it as a separate program, so installing it (`pip install ".[images]"`, included in `all`) is up to you.

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
- Paste links, or any text that contains them (a chat export, an e-mail, notes), into the Links box, or use **Import file...** (Ctrl+O) for a `.txt`, `.csv`, `.json` or HTML file. Each link's platform is detected; there is nothing to choose. The line under the box says what was found, for example `12 links found: Instagram 5, X (Twitter) 4. 2 duplicates removed. 1 not supported.`, and links from unsupported sites are listed in the run report as skipped.
- The **Queue** lists every link with its status (✓ Saved, ✕ Failed, … Waiting, ↓ 45 %, = Already saved, ■ Stopped, ⊘ Skipped) and, for a failed link, why in plain words. **Show** filters it; **Retry failed (n)** (F5) tries the failed ones again. Enter or a double-click opens the file. Right-click, Shift+F10 or the Menu key gives Open file, Open folder, Copy link, Copy error, Fix sign-in, Retry, Download again and Remove from list.
- **Export report** writes a single HTML page (it opens anywhere) and the run's CSV. Account names of the people in the posts are left out unless you choose to include them. **Show details** opens the log, with sign-ins removed.
- The download folder and the window's size are remembered (`settings.json` in your user data folder). Ctrl+Enter starts, Ctrl+L goes to the Links box.
- **Options** (Ctrl+,) sets video quality (best available, up to 1080p or 720p, audio only), what to save next to the media (post details, thumbnails, the uploader's own subtitles), how folders are laid out (platform and account, platform only, or one folder), whether to add the post title to file names, how many links download at once, the short pause between links from one platform, keeping the computer awake, and the look (same as Windows, light or dark). Changes apply to the next download; the look applies the next time the app opens. Windows High Contrast is always followed, and the window stays sharp at 150 % and 200 % display scaling.
- The **?** menu lists the keyboard shortcuts (F1), copies diagnostics for a bug report (versions and the last log lines, sign-ins removed) and opens the log folder. F6 moves between the Links box, the queue and the buttons; Alt plus a button's underlined letter selects it.
- Google Drive links try public access first and use your Drive sign-in only when Drive refuses the link.
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

`import` shows the terms notice and asks you to type `yes`; pass `--accept-terms` when running without a terminal. For Instagram only, `import instagram --password` signs in with your username and password in the terminal (asking for a security code if Instagram wants one); it is a last resort, for the same reasons as in the window.

Google Drive: public links need nothing. For files shared with your Google account, use your Firefox sign-in or a google.com cookies file (needs gdown 6 or newer). The advanced alternative, **Connect with your own Google Cloud project**, walks through creating a free Desktop OAuth client and signs in through your normal browser. Google signs you out every 7 days while that project is in Testing status, and the app tells you when that happens. A `client_secrets.json` left in the project root by older versions is copied into the Google Drive sign-in folder.

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
- `--force` downloads links again even if an earlier run saved them.
- `--quality best|1080|720|audio` picks the video quality (default: the original); `--no-metadata` leaves out the post details file.
- `--resume` finishes the last run that stopped before it was done (closed window, crash, power cut), in the same download folder. Starting a new run tells you when there is one to resume.
- Up to 3 links download at once (`--parallel N`, 1 to 6), but never two from the same platform, with a short random pause between links from one platform (8 to 15 seconds on Instagram). When a platform limits requests, the app pauses that platform for 10 minutes, tries the link once more, and keeps going with the others.
- Ctrl+C stops after the current files and records the rest as not started (press it again to quit at once); `--resume` finishes them later. In the window, **Stop** asks first, then stops within seconds; a file that was not finished is removed.
- The computer is kept from sleeping while a run is going (the screen may still turn off); `--no-keep-awake` turns that off.

What each run saves
-------------------

- Files are named `<platform>/<account>/<YYYY-MM-DD>_<post id>.<ext>` (for example `youtube/jawed/2005-04-24_jNQXAC9IVRw.webm`), numbered `_1`, `_2` for posts with several items. The date is the post's date, or the download date when the site gives none (the manifest says which). Titles stay out of file names unless you pass `--include-title` (first 60 characters). Google Drive files keep their own names under `gdrive/<file id>/`.
- Media goes into the download folder; next to it, the post's details: yt-dlp's `.info.json` (caption, upload time, uploader, counts) and thumbnail, Instaloader's `.json`, or a `.drive.json` for Drive files. Cookies are removed from `.info.json` files, and nothing is embedded into the media.
- Each link first downloads into `_staging/` and is moved into place once complete, so half-finished files never mix with the archive. An existing file is never overwritten: a different file with the same name is saved as `name (2).ext`, and an identical one is kept once.
- Every run writes `_runs/<run id>/` with `manifest.jsonl` (one line per link, written as each finishes), `manifest.csv`, and `SHA256SUMS.txt`. To check later that no file changed, run this from the download folder: `sha256sum -c _runs/<run id>/SHA256SUMS.txt`.
- In the window, a bar at the top offers **Resume** or **Dismiss** when the last run stopped early. If something goes wrong, a dialog says so (instead of the window freezing), with **Copy details** (sign-ins and your home folder removed; check before sharing, since it can include links you collected) and **Report problem**, which opens an empty problem report on GitHub for you to paste into.
- Links saved in earlier runs, even shared in another format, are recorded as "already saved" and not downloaded again. The list lives in `history.sqlite` in your user data folder (`%LOCALAPPDATA%\multisource-downloader` on Windows).
- Without ffmpeg, some videos are saved as separate video and audio files. Install ffmpeg (`winget install Gyan.FFmpeg`) for single merged files.

Troubleshooting
---------------

When a link is not saved, the app says why in plain words and what to do. The batch tool groups them at the end, for example `7 links: This post needs you to be signed in to Facebook. Run: multidownloader-signin import facebook --browser firefox`. The library's own error text is in the log.

| Message | What to do |
|---|---|
| This post needs you to be signed in to {platform}. | Add a sign-in: Sign-ins in the window, or `multidownloader-signin import <platform> --browser firefox`. |
| {platform} did not accept your sign-in. Sign in again. | The saved sign-in expired or was signed out; sign in again. |
| This post is private. Sign in with an account that can see it. | Only accounts that follow or were given access can save it. |
| {platform} is limiting requests. | Wait and run the same list again later; saved links are skipped. |
| YouTube is limiting this computer. | Wait an hour and try with fewer links. Signing in does not help with this. |
| This post was deleted or the link is wrong. | Check the link in a browser; the Internet Archive may have a copy. |
| This post is blocked in your region. | The platform does not show it in your country. |
| {platform} changed something and the downloader needs an update. | `python -m pip install -U yt-dlp` (in `penv`: `.\penv\Scripts\python.exe -m pip install -U yt-dlp`). |
| ffmpeg / Deno is not installed. | `winget install Gyan.FFmpeg`, `winget install DenoLand.Deno`. |
| Could not connect. Check your internet connection. | Check the connection and run the list again. |
| Could not save the file: the drive is full. | Free up space or choose another folder. |
| Something went wrong. Details are in the log. | For a bug report, run `python -m multidownloader.logs` and paste the output. |

Sessions and credential storage
-------------------------------

All sign-ins live in your user data folder, one folder per platform (`instagram/`, `twitter/`, `youtube/`, `googledrive/`, ...) under `sessions/`:
- Windows: `%LOCALAPPDATA%\multisource-downloader\sessions`
- macOS: `~/Library/Application Support/multisource-downloader/sessions`
- Linux: `~/.local/share/multisource-downloader/sessions`

On macOS and Linux the folders are readable only by you. Older versions kept sign-ins in `multidownloader/.sessions/` next to the code; the app moves them on first start (each file is copied and checked byte for byte before the old copy is deleted). Each platform folder holds:
- `cookies.txt`: the platform's cookies only, in Netscape format. yt-dlp and Instaloader both read it.
- `signin.json`: how you signed in, the account id, when the sign-in expires, and which version of the platform's terms notice you accepted.
- `instagram/*.session` and `googledrive/credentials.json`: Instaloader sessions and PyDrive2 tokens.

Older versions created `cookies.txt` automatically after any download. A file without a sign-in in it is now ignored, so you do not need to delete it. **Remove** in Sign-ins deletes a platform's files; **Remove all sign-ins** deletes them for every platform (your `client_secrets.json` is kept).

Testing
-------

Versions and their history are in [DEPENDENCIES.md](DEPENDENCIES.md); `requirements-lock.txt` is a snapshot of the exact versions the tests last passed with (`python scripts/snapshot_env.py` refreshes it). Before pushing, run `python scripts/check_public.py --commits origin/master..HEAD`: it fails on secrets, personal paths or e-mail addresses, AI mentions and em dashes. CI runs it on every pull request.

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

- Session files and cookies grant authenticated access. Keep the sessions folder private and never share it.
- Logs go to `app.log` in the `Logs` folder next to `sessions` on Windows (`~/Library/Logs/multisource-downloader` on macOS, `~/.local/state/multisource-downloader/log` on Linux), 1 MB per file, last 5 kept. Cookie values, passwords, sign-in headers and your home folder path are removed before anything is written. For a bug report, run `python -m multidownloader.logs` and paste its output.
- Rotate cookies or rerun authentication if downloads start failing with auth errors.
