# Changelog

## Unreleased

### Fixed

- **Linux:** the main window could not open: the queue's keyboard menu key was bound by its Windows
  name only, which Tk on Linux rejects. Found by running the window tests on Ubuntu in CI.
- **macOS:** right-click did not open the queue's menus (Tk there numbers the right button 2).
  Control-click works too.
- `requirements-minimal.txt` still listed PyDrive2, which 0.3.1 replaced: it now lists `google-auth`
  and `google-auth-oauthlib`, the libraries "Connect with your own Google Cloud project" uses.

### Tests

- Three groups of tests never ran in CI: the check that other BagIt tools accept our packages (bagit-python
  was not installed anywhere), the check of the pinned versions against pyproject.toml (packaging
  missing), and the 43 window tests on Ubuntu (no display). The `dev` extra now installs both tools,
  Ubuntu runs the tests on a virtual display, and a CI-only test fails if any of them is missing again.
- CI tests on macOS too, and a hung test run stops after 10 minutes with every thread's stack.

## 0.3.1 (2026-10-10)

Fixes found by testing every platform by hand, and security updates.

### Fixed

- **Google Drive** downloads failed in the window and in the Windows app ("'NoneType' object has no
  attribute 'write'"): without a console, gdown's progress bar had nowhere to write.
- **Instagram** posts failed after an Instagram change: Instaloader 4.15.3 fixes it. When Instaloader
  fails for a reason other than the sign-in or the post, the app now tries yt-dlp with the same
  sign-in, then gallery-dl for photo posts. A post could also sit at 0 % without explanation: requests
  now time out after 30 s, short "slow down" waits are logged and end on Stop, and long ones show as a
  rate limit with a countdown while the other platforms carry on.
- **TikTok** refused yt-dlp's default browser identity. TikTok links now send the User-Agent of the
  Firefox installed on your computer: your real browser's, the same every time.
- **Threads** links said "Threads changed something". Neither yt-dlp nor gallery-dl reads Threads, so
  they now say Threads posts cannot be downloaded yet.
- Clearer errors: "needs an update" when a downloader no longer understands a site, "blocked in your
  region" for posts blocked for your IP address, and Instagram photo posts reach gallery-dl.
- Dark mode: radio buttons and checkboxes show their dot and tick; hovered column headings are readable.
- The queue no longer collapses at 150 % display scaling.
- Retry failed counts only failures a retry can fix.
- A run that stopped early now anchors its custody log when you Resume or Dismiss it.
- The download scheduler no longer waits up to 0.2 s between links for no reason.

### Changed

- Google Drive "Connect with your own Google Cloud project" uses Google's own libraries (google-auth)
  instead of PyDrive2, and asks only for read-only access. Downloads are checked against the MD5 Drive
  gives. Connections saved by older versions still work.
- The daily update check covers Instaloader too.
- The Bluesky row in Sign-ins says there is nothing to sign in to.

### Security

- Updated urllib3, requests, httplib2, idna, pyasn1, protobuf, soupsieve, filelock and setuptools for
  open security alerts. Replacing PyDrive2 removed cryptography and pyOpenSSL, whose alerts its pins
  had blocked. No open security alerts remain.

## 0.3.0 (2026-10-10)

The first release of the redesigned app: one window for every platform, plain-language errors,
sign-ins that respect each platform's terms, and records that let you show later that nothing
changed.

### Downloading

- Paste any text with links (or import a file); each link's platform is detected, repeats are
  removed, and unsupported sites are listed as skipped.
- A queue with a status for every link in words and a symbol, Retry failed, a details log, and an
  HTML report that leaves out account names unless you include them.
- Up to 6 links at once, never two from the same platform, with polite pauses; a platform that limits
  requests is paused and tried again.
- Stop within seconds, resume a run that stopped early, keep the computer awake during a run, and a
  crash dialog instead of a frozen window.
- Image posts (photo-only X posts, carousels) and Bluesky through gallery-dl, run as a separate program.
- A simple view (one link box and Download) and a full view; Options for quality, what to save,
  folder layout, speed and the look (light, dark, or same as Windows; High Contrast is followed).
- A daily check tells you when yt-dlp or gallery-dl needs an update.

### Sign-ins

- Sign in here (the platform's own page in a small window), Use my Firefox sign-in, or Import a
  cookies file; each platform's terms notice is shown and acknowledged once. Only the platform's own
  cookies are kept, in your user data folder.

### Evidence and archiving

- Every run folder has `manifest.jsonl`, `manifest.csv`, `SHA256SUMS.txt` and an append-only,
  hash-chained `custody.log`, whose latest hash is also recorded outside the download folder.
- `msdl verify` (and Verify files) re-hashes every file and checks the custody log against its anchors.
- `msdl export` (and Export package...) writes a standard BagIt package (RFC 8493) that any BagIt tool
  can check, with the custody log's head in `bag-info.txt`.
- Run notes (topic, place, collector, notes) and per-link notes, saved with the run.
- Files are kept exactly as the platform served them by default; the manifest says when video and
  audio were joined and from which formats; Make a playable copy writes a second file when needed.
- A link saved in an earlier run is recorded as already saved instead of being downloaded again.

### Accessibility

- Every action works with the keyboard (F1 lists the keys), visible focus, WCAG 2.2 AA contrast,
  scaling at 150 % and 200 %, and a help button in the same place in every window. Screen readers do
  not yet hear status changes in the window; the command line is the fully accessible way. See
  `docs/accessibility.md`.

### The Windows app

- `MultisourceDownloader.exe` replaces the old portable YouTube app and opens in the simple view the
  first time. It includes `THIRD_PARTY_LICENSES.txt`; gallery-dl is not included (GPL-2.0). ffmpeg
  and Deno are separate installs. `--self-test` checks a build without the network or your data.
- The exe is not code-signed, so Windows SmartScreen may warn the first time: More info, Run anyway.
