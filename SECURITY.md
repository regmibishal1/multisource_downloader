# Security

## Reporting a problem

Please report security problems privately, not in a public issue: open the repository's
**Security** tab on GitHub and choose **Report a vulnerability**.

Useful to include: what you did, what happened, the version (in the window: **?**, **Copy
diagnostics**, which removes sign-ins; or `python -m multidownloader.logs`), and your operating
system. Do not include cookies, passwords or files from your sign-ins folder.

Only the latest release gets fixes.

## What the app does with your data

**Sign-ins.** The app signs in only to the platforms you choose in Sign-ins, and only to download
posts. Each platform's sign-in is stored in its own folder under `sessions`, in your user data
folder (`%LOCALAPPDATA%\multisource-downloader` on Windows, `~/Library/Application Support/multisource-downloader`
on macOS, `~/.local/share/multisource-downloader` on Linux; readable only by you on macOS and Linux):

- **Cookies** (`cookies.txt`), however you signed in: "Sign in here" (the platform's own page in a
  private window that is emptied afterwards), "Use my Firefox sign-in" (read from Firefox, and read
  again at the start of every run), or an imported cookies file. Only that platform's own cookies are
  kept; everything else in the source is dropped.
- **Instagram with a username and password**: Instaloader's session file. The password is sent only
  to Instagram and is not saved.
- **Google Drive with your own Google Cloud project**: your OAuth client file (`client_secrets.json`)
  and the token Google issues (`credentials.json`), for read-only Drive access.

**Remove** in Sign-ins deletes a platform's files. These files give access to your accounts: never
share the `sessions` folder.

**Network.** The app connects to the platforms you download from, to Google's sign-in when you
connect Drive, and once a day to pypi.org for the newest version numbers of the downloaders (turn
it off in Options). There is no telemetry.

**What it writes.** Downloads, their post details, and each run's records (`manifest.jsonl`,
`manifest.csv`, `SHA256SUMS.txt`, `custody.log`, your notes) go to the download folder you choose.
The user data folder holds settings, the list of links already saved (`history.sqlite`), the
custody logs' anchors, and logs. Logs have cookie values, passwords, sign-in headers and your home
folder path removed before anything is written. Manifests and logs still contain links and the
account names of the people in the posts: keep the download folder private and check anything
before you share it.

## The Windows app

The Windows app on the Releases page is built from the source by this repository's CI workflow
on GitHub's runners, from the commit the release tag points to, and checked there with its own
`--self-test`. It is not code-signed, so Windows SmartScreen may warn the first time; each release
lists the zip's SHA-256 and includes `SHA256SUMS.txt` to check the download against.
`THIRD_PARTY_LICENSES.txt` inside lists every bundled package. gallery-dl is never bundled; when you
install it, the app runs it as a separate program.
