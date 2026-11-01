# GitHub Release Downloader for Rubika

A personal service that runs on your own server, logs into **your Rubika account** (a user account via
[`rubpy`](https://github.com/shayanheidari01/rubika), not a bot) and is controlled entirely from your
**Saved Messages**. Browse any GitHub repository's releases, pick assets, and the files land back in
Saved Messages. No website, no dashboard, no separate bot.

## How it works

```
Saved Messages ──/get 2dust/v2rayNG──▶ service ──GitHub REST API──▶ releases & assets
      ▲                                   │
      └──── random-named ZIP (parts if big) ◀──┴── streamed to disk, size + SHA-256 verified
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # set RUBIKA_PHONE (and optionally GITHUB_TOKEN)
python main.py login        # once: phone number + SMS code -> session file
python main.py              # run the service
```

Run it permanently with systemd, `tmux` or Docker. Stop with Ctrl+C / SIGTERM: unfinished jobs are
recovered on the next start.

## Commands (send them in Saved Messages)

| Command | What it does |
|---|---|
| `/get owner/repo` or `/get https://github.com/owner/repo` | repository info + first page of releases |
| `/releases [owner/repo] [stable\|all]` | browse all releases (5 per page) |
| `/latest [owner/repo]` | latest stable release and its assets |
| `/release owner/repo tag` | open one release (`/release tag` inside an open repo) |
| `/open N` · `/page N` · `/filter stable\|all` · `/back` | navigate |
| `/asset 1 3` | download assets by list number (`zip` / `tar` = source archives) |
| `/download ID` | download by GitHub asset ID |
| `/downloads` · `/cancel [ID]` · `/retry ID` | manage jobs |
| `/status` · `/help` | server status, storage, help |

Rubika's user-account API (rubpy) has no inline buttons or callbacks, so navigation is command based.

## Behaviour and guarantees

* Only messages **you** typed in **your Saved Messages** that start with `/` are processed; other chats,
  other users and the app's own replies are ignored. Each message id is processed once, even across restarts.
* Only `github.com` / GitHub-owned hosts are ever contacted; redirects are re-validated hop by hop and the
  GitHub token is never sent to the storage host. Arbitrary URLs are rejected.
* Downloads stream to disk (no large RAM use), resume with `Range` after network failures and retry with
  exponential backoff. Size is checked, and SHA-256 is checked against GitHub's `digest` when present
  (the SHA-256 is also written in the file caption).
* **Every file is delivered as an AES-256 encrypted ZIP, always**, with a **new random 16-character password
  per file** that is written in the caption of every part (`🔑 password: …`). Open it with 7-Zip, WinRAR or
  ZArchiver (Windows Explorer cannot open AES ZIPs). The password is also kept in the job database (needed
  to caption parts after a restart), so protect `data/`. The download is stored on disk under a random name, and
  inside the ZIP it also has a random name (no extension); the ZIP itself gets another random name
  (`3fa9…c1.zip`). Captions carry only a job number and the password, never the file name, repository, tag or hash.
  The mapping back to the real name is in the "delivered" status message and in `/downloads`.
* Files above 100 MB become several ZIP volumes of 95 MB (`name.zip.001`, `.002`, …) like
  telegram-to-rubika: put all parts in one folder and open the `.001` file with 7-Zip or WinRAR. They are
  uploaded one at a time, in order. The ZIP is written straight into volumes (no extra full copy on disk).
* Limits: `MAX_DOWNLOAD_SIZE_MB` per file (default 2000), `MAX_STORAGE_MB` quota, `MIN_FREE_MB` free disk.
  No Rubika upload limit is assumed: 95 MB parts above 100 MB are the values proven by telegram-to-rubika.
* Job state lives in SQLite. After a restart, interrupted downloads resume and delivered parts are never
  sent again. If the process died *during* an upload, the part may or may not have arrived, so you are
  asked to check and `/retry` instead of risking a duplicate.
* Downloaded files are never executed. Secrets (`.env`, `*.rp` session, database) are git-ignored; the
  session file is `chmod 600`. Tokens and the phone number are redacted from logs.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

GitHub and Rubika are faked, so no credentials or network are needed.

## Configuration

See `.env.example`. `MAX_DOWNLOAD_SIZE_MB` left blank means 2000.
