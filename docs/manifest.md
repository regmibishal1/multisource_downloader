# The run folder and its manifest

Every run, from the window or the command line, writes a folder `_runs/<run id>/` inside the
download folder. The run id is the start time in UTC plus four random characters, for example
`2026-10-09T14-03-22Z_9f2c`. The folder holds:

| File | What it is |
|---|---|
| `manifest.jsonl` | One JSON object per link, written and flushed as each link finishes, so a crash loses at most the link in progress. |
| `manifest.csv` | The same rows with the columns below, for spreadsheets. |
| `SHA256SUMS.txt` | The SHA-256 of every saved file, relative to the download folder. `sha256sum -c _runs/<run id>/SHA256SUMS.txt`, run from the download folder, checks that nothing changed. |
| `run.json` | The list of links the run was asked to do. With the manifest it tells which links were not done, so an unfinished run can be resumed. |
| `notes.json` | Your notes, when you added any: `run` (`topic`, `where`, `collected_by`, `notes`) and `links` (one note per link). Each change is also a `noted` line in the custody log. |
| `custody.log` | What happened to the files, in order, one event per line, each line chained to the one before by its SHA-256. See [how-it-works.md](how-it-works.md#the-custody-log). |

The head of each custody log is also recorded in `anchors.log` in the per-user data folder
(`%LOCALAPPDATA%\multisource-downloader` on Windows), outside the download folder.

## Fields in `manifest.jsonl`

| Field | Meaning |
|---|---|
| `run_id` | The run this row belongs to. |
| `input_url` | The link exactly as it was pasted or imported. |
| `normalized_url` | The same link with tracking parameters removed and the host made canonical; used to spot repeats. |
| `platform` | `youtube`, `x`, `instagram`, `threads`, `tiktok`, `facebook`, `reddit`, `gdrive`, `bluesky`, or empty for unsupported sites. |
| `backend` | The program that downloaded it: `yt-dlp`, `instaloader`, `gdown` or `gallery-dl`. |
| `backend_version` | That program's version, for example `yt-dlp 2026.8.19`. |
| `app_version` | This app's version. |
| `status` | `saved`, `already_saved`, `failed`, `skipped` or `stopped`; `derived` for a playable copy made later. |
| `started_at`, `finished_at` | UTC times, `YYYY-MM-DDTHH:MM:SSZ`. |
| `files` | Each saved file: `path` (relative to the download folder, forward slashes), `bytes`, `sha256`, and `duplicate_of` when an identical file was already there. |
| `metadata_path` | The post details file among `files`, when there is one. |
| `post` | What the post details say: `id`, `uploader` (the account), `posted_at`, `title` (caption or title), and `date_source`: `posted` when the folder date is the post's date, `downloaded` when the site gave none. |
| `error_kind`, `error_message`, `error_action` | For a failed link: the kind of problem, the plain message shown in the app, and the suggested next step. |
| `error_detail` | The downloader's own error text, for bug reports. |
| `skip_reason` | For a skipped link: `unsupported` or `duplicate`. |
| `previous` | For `already_saved`: when and where an earlier run saved it. |
| `merged`, `formats` | For yt-dlp downloads: whether separate video and audio streams were joined (without re-encoding), and the platform's format ids of the saved streams, for example `true` and `["137", "140"]`. |
| `derived_from`, `how` | Only on `derived` rows, written by **Make a playable copy**: the original file and how the copy was made. The copy's own path and SHA-256 are in `files`. |
| `notes` | Your notes when the link was recorded: `run` (the run's topic, place, collector and notes) and `link` (this link's note). Notes added later are in `notes.json`. |
| `source_hint` | The platform name the link came with in an imported list, if any. |
| `wayback_url` | Reserved for an Internet Archive copy; empty for now. |

## Columns in `manifest.csv`

`run_id, status, platform, input_url, normalized_url, backend, files, bytes, sha256, error_kind,
error_message, started_at, finished_at, app_version, backend_version, note` (`note` is the link's note). The file is UTF-8 with a
byte order mark, so Excel opens it correctly. `files` and `sha256` list several values separated by
` | ` for posts with more than one file; `bytes` is their total. For a skipped link, `error_message`
holds the skip reason.

## Fields in `custody.log`

| Field | Meaning |
|---|---|
| `seq` | Line number, from 1. |
| `at` | UTC time of the event. |
| `event` | `run_started` (with the run's notes), `captured` (a download finished), `hashed` (a file's SHA-256), `noted` (a link's note was added or changed), `verified`, `exported`, `copied`, `anchored` (the head was recorded elsewhere), `run_finished`. |
| `run_id`, `app` | The run and the app version. |
| `detail` | Event details, for example `path` and `sha256` for `hashed`, or `method`, `seq` and `head` for `anchored`. |
| `note` | A note from the user, if any. |
| `prev` | `sha256:` and the SHA-256 of the previous line as written, without its line ending (`sha256:000...0` on line 1). |
| `hash` | `sha256:` and the SHA-256 of this line written with `"hash":""`. |

## Export packages

`msdl export` and **Export package...** write a BagIt bag (RFC 8493) of one run:

| File | What it is |
|---|---|
| `bagit.txt` | `BagIt-Version: 1.0`, UTF-8. |
| `data/` | The run's files at the same paths as in the download folder, and its run folder under `data/_runs/<run id>/`. |
| `manifest-sha512.txt`, `manifest-sha256.txt` | Every file under `data/` with its checksum. |
| `bag-info.txt` | `Source-Organization` (optional, from Options), `Bagging-Date`, `Payload-Oxum` (bytes and file count), `External-Identifier` (the run id), `External-Description` (your run notes), `Bag-Software-Agent`, `Bag-Count`, `Internal-Sender-Description`, and `Custody-Log-Head`, `Custody-Log-Seq` and `Custody-Log-Anchored`: the head of the package's copy of the custody log. |
| `tagmanifest-sha512.txt`, `tagmanifest-sha256.txt` | The checksums of the four files above. |

Any BagIt tool can check the package, for example `bagit.py --validate package-<run id>` (bagit-python from
the Library of Congress). `msdl verify --custody package-<run id>` also checks the custody log against
`Custody-Log-Head`.

## Privacy

`post.uploader` and file paths contain the account names of the people in the posts. Exported HTML
reports leave them out unless you choose to include them, but the manifest always has them. Keep
the download folder private, and turn on drive encryption (BitLocker on Windows, FileVault on macOS).
