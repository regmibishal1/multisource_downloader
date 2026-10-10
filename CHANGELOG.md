# Changelog

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
