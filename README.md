Multisource Downloader
======================

Save posts, videos and photos from YouTube, X (Twitter), Instagram, Threads, TikTok, Facebook, Reddit, Google Drive and Bluesky, from a list of links. It is built for archiving: every run records where each file came from, when, and its SHA-256, and links saved before are never downloaded twice.

There is a window (with a simple view for one link at a time) and a command line for long lists.

Install
-------

**Windows app.** Download the zip from the [Releases page](https://github.com/regmibishal1/multisource_downloader/releases) when one is published, unzip it, and run `MultisourceDownloader.exe`. It opens in the simple view the first time. To build it yourself, see [For developers](#for-developers).

**With Python 3.10 or newer** (Windows, macOS, Linux):

```powershell
python -m pip install "multisource-downloader[all] @ git+https://github.com/regmibishal1/multisource_downloader"
multisource-downloader
```

That adds four commands: `multisource-downloader` (the window), `multidownloader-batch` (lists of links), `multidownloader-signin` (sign-ins) and `msdl` (archive tools: verify, export). Instead of `all` you can pick only what you need: `instagram`, `drive`, `webview` ("Sign in here"), `images` (photo posts and Bluesky).

Two programs that are not Python packages help with videos:

- [Deno](https://docs.deno.com/runtime/getting_started/installation/) (`winget install DenoLand.Deno`): YouTube needs it to read its pages; without it some formats are missing.
- [ffmpeg](https://ffmpeg.org/download.html) (`winget install Gyan.FFmpeg`): merges the best video and audio into one file; without it some videos are saved as separate video and audio files.

First run
---------

1. Paste links into the **Links** box. Any text that contains links works: a chat export, an e-mail, notes. **Import file...** (Ctrl+O) reads a `.txt`, `.csv`, `.json` or web page. The line under the box says what was found, for example `12 links found: Instagram 5, X (Twitter) 4. 2 duplicates removed. 1 not supported.`
2. Check **Save to**. Files go to `Downloads\Multisource` in your home folder unless you choose another folder.
3. Select **Download**. The **Queue** shows each link's status in words and a symbol (✓ Saved, ✕ Failed, … Waiting, ↓ 45 %, = Already saved, ■ Stopped, ⊘ Skipped) and, for a failed link, why and what to do. **Retry failed** (F5) tries the failed ones again.

The **simple view** is one box, Save to, Download and a one-line result with **Open folder**. Switch views from the **?** menu or with Ctrl+Shift+V; start with `--simple` or `--full`. The app remembers the view, the folder, the window size and your options.

More in the window:

- Enter or a double-click opens a file. Right-click, Shift+F10 or the Menu key: Open file, Open folder, Copy link, Copy error, Fix sign-in, Retry, Download again, Remove from list. Right-click a column heading for **Fit columns**.
- **Stop** (Ctrl+.) asks first, then stops within seconds; an unfinished file is removed. If a run stops early (closed window, crash, power cut), a bar offers **Resume** next time.
- **Add notes (optional)**, above the queue: event or topic, where, collected by (a name or pseudonym) and notes, saved with the next run; **Add note...** in a link's menu adds a note to one link, before or after it downloads. Notes stay in the run folder. Leave out names if sharing them could put someone at risk. On the command line: `--topic`, `--where`, `--collected-by`, `--note`.
- Files are kept exactly as the platform served them (Options, What to save): no repairs or re-encoding, nothing embedded into the media, and when video and audio come separately the manifest says they were joined and from which formats. If a file does not play, **Make a playable copy** in its menu writes a second, remuxed file and records both. On the command line, `--repair-files` turns this off and `--keep-streams` also keeps the separate streams.
- **Verify files** re-hashes every file of the last run (or a run folder or package you choose) and checks the custody log. **Export package...** copies the run, its files and its records into a BagIt package (RFC 8493), the standard folder format archives and lawyers can check with any BagIt tool, and verifies it. Options, Archive packages, sets an optional organization name for it.
- **Export report** writes one web page and the run's CSV. Account names of the people in the posts are left out unless you choose to include them.
- **Options** (Ctrl+,): video quality (the original, up to 1080p or 720p, audio only), what to save next to the media (post details, thumbnails, the uploader's own subtitles), folder layout, the post title in file names, links at once, a short pause between links from one platform, keeping the computer awake, the look (same as Windows, light or dark) and the daily update check.
- **?**: Help (F1, a page bundled with the app), Keyboard shortcuts, Copy diagnostics for a bug report (sign-ins removed), Open log folder.
- Once a day the app asks pypi.org for the newest yt-dlp and gallery-dl version numbers; nothing about you or your links is sent. When yt-dlp is more than 45 days old and a newer one exists, a bar says so with **How to update**.

Sign-ins
--------

Public posts download without signing in. When a link needs a sign-in, the app offers to set one up after the run, and **Sign-ins** (top right) shows every platform's status.

Most platforms' terms do not allow automated downloading. This app is meant for preserving public-interest evidence and your own content. You are responsible for how you use it. If you plan to use files in legal proceedings, ask a lawyer. Before the first sign-in for a platform, the app shows what that platform's terms say and asks you to confirm. Facebook allows one account per person, in your real name, so do not create another Facebook account for this app. On other platforms, if you sign in at all, use an account you can afford to lose.

Ways to sign in:

- **Sign in here** (Instagram, X, Facebook, TikTok, Threads, Reddit): the platform's own sign-in page in a small window. Sign in as usual, including any security code; the window closes by itself. It starts from an empty private profile every time, and your password goes only to the platform. Your password manager may not fill this window, but paste works. A minute before its 10 minutes run out, the app asks "Still there?" with **Keep waiting**. Needs the `webview` extra and, on Windows, Microsoft's WebView2 Runtime, which Windows 11 already has. Not offered for YouTube and Google Drive, because Google blocks signing in inside other apps.
- **Use my Firefox sign-in**: the app reads the platform's cookies from Firefox, where you are already signed in and your password manager works. Nothing is exported, and the app re-reads Firefox at the start of every run. Chrome, Edge and other Chromium browsers lock their sign-ins on Windows, so use Firefox or a cookies file there.
- **Import a cookies file**: a `cookies.txt` exported with the "Get cookies.txt LOCALLY" extension (Chrome, Edge) or "cookies.txt" (Firefox). Do not use the extension named just "Get cookies.txt". Only the platform's own cookies are kept, so you can delete the export afterwards.
- **Username and password** (Instagram only, under "More ways to sign in"): not recommended, because Instagram is more likely to lock accounts that sign in from scripts.

Google Drive: public links need nothing. For files shared with your Google account, use your Firefox sign-in or a google.com cookies file. The advanced alternative, **Connect with your own Google Cloud project**, walks through creating a free Desktop OAuth client. Google signs you out every 7 days while that project is in Testing status, and the app tells you when that happens.

Coming from the old portable YouTube app? Add its cookies file once in **Sign-ins**, YouTube, "Import a cookies file"; it is in `%LOCALAPPDATA%\multisource-downloader\portable\cookies.txt`.

From the command line:

```powershell
multidownloader-signin status
multidownloader-signin import instagram --browser firefox
multidownloader-signin import x --cookies C:\path\to\cookies.txt
multidownloader-signin remove --all
```

`import` shows the terms notice and asks you to type `yes`; pass `--accept-terms` when running without a terminal. `import instagram --password` signs in with your username and password in the terminal; it is a last resort, for the same reasons as in the window.

Lists of links (command line)
-----------------------------

```powershell
multidownloader-batch links.txt --out-dir D:\archive
multidownloader-batch grouped_by_source.json --out-dir D:\archive --dry-run
```

The input can be a text file with links, a CSV, a JSON list, or the `grouped_by_source.json` of loot_report_scraper.

- `--dry-run` prints the plan without downloading; `--limit` and `--per-source-limit` restrict how many links are tried.
- `--quality best|1080|720|audio` picks the video quality (default: the original); `--no-metadata` leaves out the post details file; `--include-title` adds the first 60 characters of the title to file names.
- `--force` downloads links again even if an earlier run saved them. `--resume` finishes the last run that stopped before it was done, in the same download folder.
- Up to 3 links download at once (`--parallel N`, 1 to 6), never two from the same platform, with a short random pause between links from one platform (8 to 15 seconds on Instagram). When a platform limits requests, the app pauses that platform for 10 minutes, tries the link once more, and keeps going with the others.
- Ctrl+C stops after the current files and records the rest as not started (press it again to quit at once).
- The computer is kept from sleeping during a run (the screen may still turn off); `--no-keep-awake` turns that off.
- Exit codes: `0` everything tried was saved, `1` some links failed, `2` nothing was tried because every link was from an unsupported site.

The command line works fully with screen readers, and failures are grouped at the end with what to do, for example `7 links: This post needs you to be signed in to Facebook. Run: multidownloader-signin import facebook --browser firefox`.

What each run saves
-------------------

- Files are named `<platform>/<account>/<YYYY-MM-DD>_<post id>.<ext>` (for example `youtube/jawed/2005-04-24_jNQXAC9IVRw.webm`), numbered `_1`, `_2` for posts with several items. The date is the post's date, or the download date when the site gives none. Options can drop the account or platform folders. Google Drive files keep their own names under `gdrive/<file id>/`.
- Next to the media: the post's details (yt-dlp's `.info.json`, Instaloader's `.json`, or a `.drive.json`) and the thumbnail. Cookies are removed from `.info.json` files, and nothing is embedded into the media.
- Each link downloads into `_staging/` first and is moved into place once complete. An existing file is never overwritten: a different file with the same name is saved as `name (2).ext`, an identical one is kept once.
- Every run writes `_runs/<run id>/` with `manifest.jsonl`, `manifest.csv`, `SHA256SUMS.txt` and `custody.log`, an append-only record of what happened to the files whose latest hash is also kept outside the download folder. Every field is described in [docs/manifest.md](docs/manifest.md), and [docs/how-it-works.md](docs/how-it-works.md) explains what each downloader does to a file and what the records can and cannot prove.
- `msdl export <download folder>\_runs\<run id> --bag D:\handover` writes `package-<run id>/` (add `--zip` for a zip too, `--source-organization` for your organization): the run's files under `data/`, SHA-512 and SHA-256 manifests, and `bag-info.txt` with the run id, your notes and the custody log's head. The package includes the manifest, so it contains the account names of the people in the posts; share it only with people who need it.
- To check later that nothing changed, run `msdl verify <download folder>\_runs\<run id>` (or a package folder): it re-hashes every file and says "All 214 files match." or lists the changed and missing ones (exit code 0 or 1). `--custody` also checks the custody log against its anchors. `sha256sum -c _runs/<run id>/SHA256SUMS.txt` from the download folder works too.
- Links saved in earlier runs, even shared in another format, are recorded as "already saved". The list is `history.sqlite` in your user data folder.

Supported platforms and what downloads them: YouTube, X, TikTok, Threads, Reddit and Facebook with yt-dlp; Instagram with Instaloader; Google Drive with gdown and PyDrive2; Bluesky and image posts (photo-only X posts, Reddit, TikTok and Facebook photo posts) with gallery-dl. Image-only Threads posts are not supported yet.

Troubleshooting
---------------

When a link is not saved, the app says why in plain words and what to do; the library's own error text is in the log.

| Message | What to do |
|---|---|
| This post needs you to be signed in to {platform}. | Add a sign-in: **Fix sign-in** in the queue, Sign-ins in the window, or `multidownloader-signin import <platform> --browser firefox`. |
| {platform} did not accept your sign-in. Sign in again. | The saved sign-in expired or was signed out; sign in again. |
| This post is private. Sign in with an account that can see it. | Only accounts that follow or were given access can save it. |
| {platform} is limiting requests. | Nothing: the app waits and tries again. Fewer links at once helps; saved links are skipped next time. |
| YouTube is limiting this computer. | Wait an hour and try with one link at once. Signing in does not help with this. |
| This post was deleted or the link is wrong. | Check the link in a browser; the Internet Archive may have a copy. |
| This post is blocked in your region. | The platform does not show it in your country. |
| {platform} changed something and the downloader needs an update. | Update yt-dlp: `python -m pip install -U "yt-dlp[default]"`, or the newest Windows app. |
| ffmpeg / Deno is not installed. | `winget install Gyan.FFmpeg`, `winget install DenoLand.Deno`. |
| Could not connect. Check your internet connection. | Check the connection, then **Retry failed**. |
| Could not save the file: the drive is full. | Free up space or choose another folder. |
| Something went wrong. Details are in the log. | **?**, **Copy diagnostics** (or `python -m multidownloader.logs`) and paste it into a [problem report](https://github.com/regmibishal1/multisource_downloader/issues). |

Accessibility
-------------

What works today: every action with the keyboard (F1 lists the keys), visible focus, WCAG 2.2 AA contrast in the light, dark and Windows High Contrast looks, sharp scaling at 150 % and 200 %, and a help button in the same place in every window. What does not yet: screen readers do not hear status changes in the window (a Tk 8.6 limit), so the final result also goes into the window title, and the command line is the fully accessible way to run the app. Details: [docs/accessibility.md](docs/accessibility.md).

Security and responsible use
----------------------------

- No telemetry. The app talks only to the platforms you download from and, once a day if you leave it on, pypi.org.
- Sign-ins live in your user data folder, one folder per platform under `sessions/` (`%LOCALAPPDATA%\multisource-downloader\sessions` on Windows, `~/Library/Application Support/multisource-downloader/sessions` on macOS, `~/.local/share/multisource-downloader/sessions` on Linux; readable only by you on macOS and Linux). They grant access to your accounts: never share that folder. **Remove** in Sign-ins deletes a platform's files.
- Logs (`app.log` in the `Logs` folder next to `sessions` on Windows, `~/Library/Logs/multisource-downloader` on macOS, `~/.local/state/multisource-downloader/log` on Linux) have cookie values, passwords, sign-in headers and your home folder path removed before anything is written; 1 MB per file, last 5 kept.
- Manifests contain the account names of the people in the posts. Keep the download folder private and turn on drive encryption (BitLocker on Windows, FileVault on macOS). Exported reports leave account names out by default.
- Respect each platform's terms and your local law.

License: MIT (see [LICENSE](LICENSE)). The Windows app lists the license of every package it includes in `THIRD_PARTY_LICENSES.txt`. gallery-dl is GPL-2.0, so the app never includes or imports it: it runs it as a separate program when you install it.

For developers
--------------

Layout: `multidownloader/` is the engine (platform table, link detection, runs and manifests, scheduler, error messages, sign-ins, backends) and has no window code; `multidownloader/gui/` and `multisource_downloader_ui.py` are the window; `tests/` uses unittest.

The project's own environment is `penv` with exact versions in `requirements.txt`; always run commands through it:

```powershell
python -m venv .\penv
.\penv\Scripts\python.exe -m pip install -r requirements.txt
.\penv\Scripts\python.exe multisource_downloader_ui.py
.\penv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Tests that open windows run only in CI or with `MSDL_GUI_TESTS=1`, because a test window takes the keyboard focus for a moment. Lint with `ruff check .`.

Versions and their history are in [DEPENDENCIES.md](DEPENDENCIES.md); `requirements-lock.txt` is a snapshot of the exact versions the tests last passed with (`python scripts/snapshot_env.py` refreshes it). Before pushing, run `python scripts/check_public.py --commits origin/master..HEAD`: it fails on secrets, personal paths or e-mail addresses, AI mentions and em dashes. CI runs it, ruff and the tests on Windows and Ubuntu with Python 3.10 and 3.13.

Windows app: `python -m pip install pyinstaller`, then `python scripts/build_exe.py` writes `dist/MultisourceDownloader/` and a zip, with `LICENSE` and `THIRD_PARTY_LICENSES.txt` (from `scripts/third_party_licenses.py`). `MultisourceDownloader.exe --self-test --report self-test.txt` checks a build without the network or your data: a hidden window, one fake download with its manifest, a fake sign-in saved and removed, and the bundled components. CI builds the app, runs the self-test and keeps the zip for 14 days.

loot_report_scraper: its `run_downloader.py` calls `multidownloader.batch` with the latest `grouped_by_source.json`. Point it at this project:

```powershell
$env:MULTIDOWNLOADER_PYTHON = "C:\path\to\multisource_downloader\penv\Scripts\python.exe"
$env:MULTIDOWNLOADER_ROOT = "C:\path\to\multisource_downloader"
.\penv\Scripts\python.exe run_downloader.py --limit 20
```
