# seesaw-media-downloader

Download photos, videos and PDFs from your **Seesaw family feed** to a local folder —
originals, not the lossy PDF/thumbnail export the web app offers.

> For family members downloading their own children's content. Seesaw has no public API,
> so this tool signs in with your own credentials and uses the same private endpoints the
> web app does. Use it at your own risk, and keep the default rate limits.

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and the reverse-engineering
notes.

- [x] Config resolution (flag → env → `.env` → default) and login
- [x] `list`
- [x] `--since`
- [x] `--all`
- [x] skip-existing (the default)

## Install

```bash
uv venv --python 3.12
uv pip install -e ".[dev,login]"
uv run playwright install chromium
```

Playwright lives in a `login` extra because only `seesaw-dl login` drives a browser.
Downloading needs neither it nor Chromium — which is what makes the Docker image below
small, and what stops it from being able to log in.

## Usage

Sign in once — this is the **only** command that opens a browser:

```bash
seesaw-dl login
```

Seesaw protects family sign-in with a reCAPTCHA, so the browser opens with your
credentials prefilled and you complete the challenge yourself. The session is then cached
(`~/.config/seesaw-dl/session.json`, `0600`) and reused indefinitely — until Seesaw
rejects it, at which point you are told to run `login` again.

Everything after that is non-interactive and needs no credentials at all. `list` reads the
feed and writes nothing:

```bash
seesaw-dl list
```

```bash
seesaw-dl list --json
```

Limit the window to recent posts — either an absolute date or a number of days back:

```bash
seesaw-dl list --since 2026-01-31
```

```bash
seesaw-dl list --since 30d
```

Give `list` an `--out` and it also reports what you already have, which is how you see
what is new:

```bash
seesaw-dl list --since 30d --out ~/Seesaw
```

An absolute date covers the **whole** of that day; `30d` is a rolling window from now.
Both are interpreted in your local timezone, matching the folder layout and EXIF stamps.

Then download for real. Files already on disk are skipped, so re-running is cheap:

```bash
seesaw-dl download --out ~/Seesaw
```

```bash
seesaw-dl download --out ~/Seesaw --all
```

`--all` re-fetches everything, ignoring what is already on disk; `--no-all` restores the
default, which is useful if `SEESAW_DOWNLOAD_ALL` is set in your `.env`.

Files land as `<year>/<YYYY-MM-DD>/`, with a `.json` sidecar per post holding its
caption, class, child and timestamp:

```
~/Seesaw/
  manifest.json
  2026/2026-05-14/
    2026-05-14T09-12-03_abcd1234_p1.jpg
    2026-05-14T09-12-03_abcd1234_p2.jpg
    2026-05-14T09-12-03_abcd1234.json
```

Configuration comes from a CLI flag, an environment variable, a `.env` file, or a default —
in that order. See `.env.example`.

| Input | Flag | Env var | Required? | Default |
|---|---|---|---|---|
| Email | `--email` | `SEESAW_EMAIL` | `login` only | — |
| Password | `--password` | `SEESAW_PASSWORD` | `login` only | — |
| Child | `--child` | `SEESAW_CHILD` | if the account has >1 child | — |
| Output dir | `--out` | `SEESAW_OUTPUT_DIR` | `download` only | — |
| Download all | `--all/--no-all` | `SEESAW_DOWNLOAD_ALL` | no | `false` (skip existing) |
| Since | `--since` | `SEESAW_SINCE` | no | unset |
| Concurrency | `--concurrency` | `SEESAW_CONCURRENCY` | no | `4` |
| Session cache | `--session-file` | `SEESAW_SESSION_FILE` | no | `~/.config/seesaw-dl/session.json` |
| JSON output | `--json` | `SEESAW_JSON` | no | `false` |
| Log level | `--log-level` | `SEESAW_LOG_LEVEL` | no | `info` |

Your password is never logged, and the session cache is written with `0600` permissions.
`download` never reads your password — only the cached session.

## One child per run

A run downloads exactly one child. With a single child on the account there is nothing to
choose and `--child` is optional; with more than one, the run stops and lists the names
rather than guessing:

```bash
seesaw-dl download --out ~/Seesaw/robin --child "Robin Rivera"
```

`--child` accepts a full name, any unique part of one (`Robin`), or a person id; matching
ignores case. Give each child its own `--out` directory — the child's name is no longer in
the path, so two children sharing one directory would interleave in the same date folders.

## Timestamps

Seesaw strips EXIF from the media it serves, so downloads would otherwise import into
Apple Photos, Immich or Lightroom in download order instead of chronological order. Every
download is stamped: JPEGs get EXIF **`DateTimeOriginal`** (with a UTC offset) set to the
post time, and the caption in `ImageDescription`; every file gets its modification time set
too, for formats that carry no EXIF. The image data itself is never re-encoded.

`DateTimeOriginal` is what those libraries read first, so your photos stay in the right
chronological order no matter what the file's own create or modify dates say — copying or
re-downloading a file cannot move it on the timeline.

The stamp is the **post date** — Seesaw's API exposes no capture date at all, so a photo
posted a week after it was taken carries the post time. Each file records that caveat in
its EXIF `UserComment` rather than passing the time off as camera truth.

## What it does not do

Browsing Seesaw in a web browser marks posts as read. This tool only ever issues `GET`
requests and never calls `item/update_seen_state_v2`, so downloading leaves your unread
state alone.

## Run in Docker

Signing in still happens on the host — Seesaw protects family sign-in with a reCAPTCHA,
which needs a real browser and a real person. But sessions stay valid for a long time, so
once you have one, downloads run unattended in a container that has no browser, no
password, and no way to log in: the image omits the `login` extra entirely.

```bash
seesaw-dl login          # on the host, once
docker compose run --rm seesaw
```

The container downloads and exits — there is nothing to keep running between runs.
Schedule that command with cron, systemd or your NAS task scheduler rather than leaving a
container up.

Everything is configured with environment variables; [`docker-compose.yml`](docker-compose.yml)
reads them from your shell or a `.env` beside it:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TZ` | `UTC` | Timezone for the dated folders and EXIF stamps |
| `SEESAW_OUTPUT_HOST_DIR` | `./out` | Host directory to download into |
| `SEESAW_SESSION_HOST_FILE` | `~/.config/seesaw-dl/session.json` | Session written by `login` |
| `PUID` / `PGID` | `1000` | User the container runs as |
| `SEESAW_CHILD` | — | Required only if the account has more than one child |
| `SEESAW_SINCE`, `SEESAW_DOWNLOAD_ALL`, `SEESAW_CONCURRENCY`, `SEESAW_LOG_LEVEL` | see [`.env.example`](.env.example) | Same meanings as the flags |

The host `.env` is deliberately *not* passed in: it holds your password, and the container
has no use for credentials it cannot log in with. The session file is mounted read-only,
which is all `download` needs — it reads a session and never writes one.

Flags still work, and `list` is available for a dry run:

```bash
docker compose run --rm seesaw download --since 30d
docker compose run --rm seesaw list --since 30d
```

### On a NAS

`PUID`/`PGID` must match the owner of your output share, or the files land unreadable — a
Synology admin is typically `1026:100`, a QNAP user `1000:100`. Run `id` over SSH to
check. The same user has to be able to read `session.json`, which `login` writes `0600`.

```bash
PUID=1026 PGID=100 TZ=America/Los_Angeles \
  SEESAW_OUTPUT_HOST_DIR=/volume1/photo/Seesaw \
  docker compose run --rm seesaw
```

`docker compose run --rm --user 1026:100 seesaw` overrides the user for a single run.

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Coverage is measured with branch coverage and fails below 85%:

```bash
uv run pytest -q --cov --cov-report=term-missing
```

`auth.py` is excluded — it drives a real browser through Playwright and cannot run without
a human and a reCAPTCHA.

## License

MIT — see [LICENSE](LICENSE).
