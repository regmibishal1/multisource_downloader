# How it works

This page explains, in plain language, what the app does to each file and what its records can
and cannot prove, so that someone who collected material with it can explain the process later
(Berkeley Protocol on Digital Open Source Investigations, para. 25).

## From a link to a saved file

1. **Detect.** Each link's platform is found from its address. Tracking parameters are removed and
   share formats are rewritten to one form, only to recognise repeats; the link as given is kept in
   the manifest.
2. **Download into staging.** The link is downloaded into `_staging/<run>_<job>/` inside the download
   folder by one program:
   - **yt-dlp** (YouTube, X, TikTok, Threads, Reddit, Facebook) asks the platform for the post and
     saves the media file the platform serves. When the best video and the best audio are separate
     streams, yt-dlp joins them with ffmpeg into one file without re-encoding (the streams are copied
     as they are), and the manifest records `"merged": true` with the format ids, for example
     `["137", "140"]`. Without ffmpeg it saves one file the platform already joined, or the two
     streams. With **Keep files exactly as the platform served them** (on by default) yt-dlp does not
     repair or remux the file after download, so its bytes are what the platform sent; **Also keep
     the separate video and audio streams** saves those too.
   - **Instaloader** (Instagram) saves the photos and videos of the post as served.
   - **gdown** and **PyDrive2** (Google Drive) save the file exactly as stored in Drive.
   - **gallery-dl** (Bluesky, and photo posts on other platforms) saves the images as served. It runs
     as a separate program.
   Next to the media each program writes the post's details (`.info.json`, `.json` or
   `.drive.json`). The app removes cookies from those details and never embeds anything into the
   media.
3. **Hash.** As soon as a download finishes, the app computes the SHA-256 of every file in staging
   and writes a `hashed` line to the run's custody log.
4. **Move into place.** Each file is moved (not copied) into the download folder at its final name.
   An existing file is never overwritten.
5. **Record.** The link's result, its files and their SHA-256 go into `manifest.jsonl` straight away;
   `manifest.csv` and `SHA256SUMS.txt` are written when the run finishes. The manifest also records
   which program and version did the download.

## Playable copies

A file kept exactly as served sometimes does not play in every player. **Make a playable copy** (a
saved link's menu) writes a second file next to it, `<name> (playable).<ext>`, with ffmpeg copying the
streams into a new container without re-encoding. The original is never changed. The copy is added
to `SHA256SUMS.txt`, gets a `derived` row in the manifest saying which file it came from and how, and
`copied` and `hashed` lines in the custody log.

## The custody log

Every run has `custody.log` in its run folder: one line per event (`run_started`, `captured`,
`hashed`, `verified`, `exported`, `anchored`, `run_finished`), each with the time in UTC and the app
version. Lines are only ever added. Each line contains the SHA-256 of the line before it, so changing
any one line breaks the chain from that point.

A chain on its own does not stop someone from rewriting the whole file and recomputing every hash.
So the latest hash (the "head") is also written outside the log:

- **On this computer**: when a run finishes (and every 100 events or 15 minutes during long runs),
  the head goes into `anchors.log` in the per-user data folder, not the download folder. A copy of
  the download folder cannot carry it, so edits to a copy show.
- **In an export package**: the package's `bag-info.txt` records the head and its line number, and
  the package's own checksums cover the log.

`msdl verify --custody <run folder or package>` recomputes the chain, compares it with every anchor
it finds, and compares the files' recorded hashes with the log's `hashed` lines. It then says the
strongest thing that is true, for example "Unchanged since the package was exported on 9 Oct 2026
06:00 UTC", or "The log's own checks pass, but it is only anchored on this computer".

## What this can and cannot prove

- It can show that files and the log have not changed **since an anchor**: since the run finished on
  this computer, or since a package was exported and handed over.
- It cannot prove what happened **before the first anchor**. Someone with access to this computer
  between the download and the anchor, or able to edit both the log and `anchors.log`, could change
  things unnoticed. Exporting a package soon after a run and giving a copy to someone else shortens
  that window.
- It cannot prove that the platform served exactly these bytes, or that the post is authentic. The
  app records what it received, from where and when.
- Times come from this computer's clock.
