# GitHub Release Downloader for Rubika

A personal service that runs on your own server, logs into **your Rubika account** (a user account via
[`rubpy`](https://github.com/shayanheidari01/rubika), not a bot) and is controlled entirely from your
**Saved Messages**. Browse any GitHub repository's releases, pick assets, and the files land back in
Saved Messages. No website, no dashboard, no separate bot.

## How it works

```
Saved Messages ──/get 2dust/v2rayNG──▶ service ──GitHub REST API──▶ releases & assets
      ▲                                   │
      └────── the file (split if big) ◀───┴── streamed to disk, size + SHA-256 verified
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # set RUBIKA_PHONE (and optionally GITHUB_TOKEN)
python main.py login        # once: phone number + SMS code -> session file
python main.py              # run the service
```

Optional: install 7-Zip (`apt install p7zip-full`) so files above 100 MB are split into 95 MB `.7z.001…`
volumes (the same approach as telegram-to-rubika). Without it a plain binary split is used.

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
