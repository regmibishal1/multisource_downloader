# Keeping the downloads working

The app's own code rarely breaks. What breaks is the platforms: they change their pages and APIs
without warning, and the programs the app uses to read them (yt-dlp, Instaloader, gallery-dl, gdown)
need updates to follow. Unit tests cannot see that; only real downloads can. Keeping those programs
current, and checking them against the real platforms, is a core part of maintaining this app.

Examples from October 2026: TikTok started refusing yt-dlp's default browser identity; an Instagram
API change broke Instaloader 4.15 (fixed in 4.15.3); Threads has never had a reader in yt-dlp or
gallery-dl. None of these showed up in the unit tests.

## What watches for breakage

| What | When | Where it shows up |
|---|---|---|
| Canary (`.github/workflows/canary.yml`) | Every Monday, and on demand | Runs the smoke test twice on Windows: with the exact versions in `requirements.txt`, and with the newest downloaders. A broken platform opens a GitHub issue titled "Canary: a platform stopped working", or adds to the open one. |
| The app's update check | Once a day, in the app | A bar when yt-dlp is old or gallery-dl or Instaloader has a newer version. |
| Dependabot | Monthly, plus security alerts | Pull requests for `pyproject.toml` and the CI actions; treat them as reminders (below). |

The canary cannot use sign-ins, and some platforms block the shared networks GitHub runs on. A link
that only fails on the canary is marked `"ci": false` in `scripts/smoke_links.json`, with a
`"ci_note"` saying why; run those locally.

## The smoke test

```powershell
.\penv\Scripts\python.exe scripts\smoke.py                  # public links, no sign-ins
.\penv\Scripts\python.exe scripts\smoke.py --with-signins   # with a temporary copy of your sign-ins
.\penv\Scripts\python.exe scripts\smoke.py --only tiktok,instagram --keep
```

It downloads every link in `scripts/smoke_links.json` through the app's own code, compares each result
with what is expected, and verifies the run with its custody log. Your data folder is never used: the
app's folders and the download folder point at a temporary folder, and `--with-signins` copies your
sign-ins into it first. It prints a table (`ok`, `BROKEN` or `not run` per link) and exits 0 when every
link behaved as expected. `--markdown` and `--json` write the report to a file.

Each link has an expectation: `saved`, `failed:<error kind>` (for example `failed:not_found` for a
removed video, so the plain error message is checked too) or `skipped`. When a post disappears, replace
its link with another public, stable, harmless one and check it by hand once.

## When something breaks

1. **Read the canary issue or the smoke table.** If the "newest" run works and the "pinned" run does
   not, an update fixes it: go to step 2. If both fail, the downloader itself is broken: look for an
   open issue in its own project (yt-dlp, Instaloader, gallery-dl, gdown) and for a workaround there.
   Workarounds in the app stay within the project's rules: no rotating identities, accounts or IP
   addresses, and no other evasion (see the README's sign-in section).
2. **Update in `penv`**, as `DEPENDENCIES.md` describes: save the package list, install the new
   version, update its pin in `requirements.txt` (and the lowest version in `pyproject.toml` when the
   app now needs it), run the unit tests, run the smoke test, take a new snapshot with
   `scripts/snapshot_env.py`, and add a line to `DEPENDENCIES.md`.
3. **Pull request**, with the smoke table before and after in the description. CI must be green.
4. **Release** in a batch with other merged work (`CHANGELOG.md`, version, CI-built Windows app,
   its self-test run locally, then the GitHub release). A fix for a platform that stopped working is a
   good reason to release soon, because users of the Windows app only get it from a release.

## Dependabot pull requests

They change one file, while `requirements.txt` and `requirements-lock.txt` must stay in step with
`penv`. Do the update as in step 2 above, then close the Dependabot pull request with a note pointing
to yours. Security alerts are the exception to waiting for a batch: fix them promptly.
