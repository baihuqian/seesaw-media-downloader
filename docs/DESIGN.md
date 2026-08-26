# Design

## Why this exists

Seesaw has no public API for family members, and its built-in export is lossy: journals
come out as a PDF, and a post with several photos exports only the first thumbnail. This
tool signs in as a family member and pulls the original media.

## Approach

Playwright drives a real Chromium **only to sign in**. Once the family feed loads we export
the browser's storage state (cookies) and the `_xsrf` token, then close the browser.
Everything after that — feed pagination and media downloads — runs over plain HTTP with that
material. This is much faster than DOM driving and far less brittle than parsing the SPA's
markup (the approach prior art takes).

### Seesaw's private API, as observed live

| Endpoint | Purpose |
|---|---|
| `GET /api/person/parent/dashboard_v3` | The parent, plus `parent.children.objects[]` |
| `GET /api/person/parent/child_classes?child_id=` | `{"objects": [...]}` — active *and* archived classes |
| `GET /api/person/parent/class_feed?child_id=&class_id=&limit=&start_key=` | Journal items; cursor pagination via `items.last_key` → `start_key` |
| `GET /api/item_v2?item_id=` | The **full** item, with every page |

**The feed only ever returns page one of a multi-page item.** An item reporting
`num_pages: 2` still arrives with a single page object, so anything multi-page must be
re-fetched through `item_v2`. This is the same gap that makes Seesaw's own export lossy,
and a feed-only implementation would silently reproduce it.

Media lives at `assets.seesaw.me` behind a signed URL whose tail (`:::ts:::ttl:::1:::sig`)
is re-issued over time. The **storage path** is therefore the stable identity used for the
manifest, not the URL. `imaging.seesaw.me` URLs are display renditions and are only ever a
last resort.

- Same-origin JSON API under `https://app.seesaw.me/api/...`.
- Requests carry session cookies plus an `_xsrf` token, and the housekeeping params
  `_bundle`, `_release` (e.g. `prod_2026-08-25.3`) and `_tz_offset`.
- Nothing about this is documented or supported. When it changes, the client raises
  `ApiContractError` with what it expected versus what it got, and endpoint discovery
  re-runs in the browser.

## Modules

| Module | Responsibility |
|---|---|
| `cli.py` | Typer commands: `login`, `download`. |
| `config.py` | One precedence chain for every input: flag → env → `.env` → default. |
| `logging.py` | Level-aware reporter. Redaction is literal-first: known secret values are registered and blanked, rather than pattern-scrubbing prose. |
| `auth.py` | `SessionStore` (`0600` cache) and the Playwright login, incl. `--headful`. |
| `errors.py` | The deliberate exception types. |
| `api.py` | Read-only HTTP client over the endpoints above, with retries and `ApiContractError` on shape changes. |
| `models.py` | `Child`, `SchoolClass`, `FeedItem`, `MediaAsset` — narrow views over wide payloads. |
| `dates.py` | Parses `--since` (`YYYY-MM-DD` or `Nd`) into a local-time instant. |
| `planner.py` | Walks every child and class into a `Plan`; the single source of truth for `--list-only` and downloads alike. |
| `render.py` | Table and newline-delimited JSON rendering of a plan. |
| `downloader.py` | Bounded-concurrency async downloads, atomic writes, per-post sidecars, EXIF stamping on arrival. |
| — | Endpoint discovery turned out to be unnecessary: the endpoints are stable constants in `api.py`, and a change surfaces as `ApiContractError` rather than being silently guessed at. |
| `manifest.py` | `manifest.json` index backing the skip-existing default. |

## Login and sessions

Seesaw gates family sign-in behind a **reCAPTCHA**. Confirmed live: `POST /api/auth/login`
returns `200` with `{"recaptcha_required": true}` even for correct credentials. Fully
headless login is therefore not achievable, and defeating the check is out of scope. The
CLI splits cleanly along that line.

**`login` is the only command that opens a browser.** It goes to `#/login?role=parent` —
the URL says *parent* even though the UI says *Family Member*, and a cold load can still
land on the role picker, which is handled by clicking through it. The email and password
are prefilled from flag/env/`.env`, and the "Family Member Sign In" button is clicked
(pressing Enter does not submit). A headless attempt runs first; when the login response
comes back asking for a reCAPTCHA, it fails with a message naming `--headful` instead of a
vague timeout. Under `--headful` the window opens prefilled, the user solves the challenge
and submits, and we wait for the family feed before capturing the session.

**Sessions are persisted and reused indefinitely.** The captured storage state (cookies)
and `_xsrf` are written atomically to `~/.config/seesaw-dl/session.json` at `0600`. There
is deliberately **no expiry guess**: because re-login needs a human, throwing a working
session away on a timer would force needless manual sign-ins. The session is used until
Seesaw itself rejects it, at which point a `401`/`403` becomes "run `seesaw-dl login`
again".

**Every other command is non-interactive.** `download` calls `load_session()`, which never
launches a browser and never reads the password — with no cached session it explains what
to run and exits non-zero. A repeat `login` reuses the cache and returns in about a second.

## The `--since` window

`YYYY-MM-DD` resolves to local midnight, so the named day is fully included; `Nd` is a
rolling window from now. Both are local, matching the folder layout and the EXIF stamps —
a cutoff interpreted in another zone would silently gain or lose a day's posts.

Pagination stops early once a window is in effect, but **not** at the first item older than
the cutoff: a pinned or back-dated post would truncate the run. Older items are skipped
individually, and pagination stops only when an entire page yields nothing new.

## Timestamps and photo libraries

Seesaw serves **re-encoded composites with no EXIF whatsoever** — verified on live
downloads, where a JPEG arrives carrying only JFIF and ICC segments. Left alone, those
files import into Apple Photos, Immich or Lightroom in *download* order rather than in the
order the moments happened, because those libraries sort on EXIF `DateTimeOriginal` and
fall back to file mtime.

**EXIF is the authoritative field, deliberately.** `DateTimeOriginal` is what Photos,
Immich and Lightroom read first, so once it is set the file's own create/modify dates stop
mattering for viewing order — copying, syncing or re-downloading a file cannot disturb
where it lands on the timeline. The mtime is set as well, but only as the fallback for
formats that carry no EXIF.

`metadata.py` therefore stamps every download:

| File type | What gets set |
|---|---|
| JPEG | **`DateTimeOriginal`** (authoritative), plus `DateTimeDigitized`, `DateTime` and `OffsetTime*` so the time is not naive; `ImageDescription` gets the caption; and the file mtime. |
| Everything else (video, PDF, PNG) | File mtime only — the fallback every library uses. |

EXIF is *inserted*, never re-encoded: the compressed image data is byte-identical before
and after (there is a test pinning this).

### Timezones

The stamp preserves the timezone the timestamp arrives with; only a naive datetime is
interpreted as local. Feed timestamps already carry the offset the post was made in, and
rewriting that into the downloading machine's zone would make the same post stamp
differently on a laptop and on a server. CI runs the suite twice, once in the runner's UTC
and once under `TZ=Asia/Kolkata`, because this bug passed locally and only failed on a UTC
runner.

### The honest caveat

The stamped time is the **post date**, not the capture date. Seesaw's API exposes no
capture time anywhere: an item carries a single `create_date`, and the epoch embedded in a
signed asset URL is the signature's issue time — its companion `1209600` is a 14-day TTL —
not when the shutter fired. A photo posted days after it was taken therefore gets the post
time. Rather than let that pass as camera truth, every stamped file says so in its EXIF
`UserComment`.

## Output layout

```
<out>/
  manifest.json
  <Child Name>/
    2026/
      2026-05-14/
        2026-05-14T09-12-03_<postid>_1.jpg
        2026-05-14T09-12-03_<postid>.json
```

## Downloading

Assets stream to a `.part` file and are `os.replace`d into position only once complete and
length-checked, so an interrupted run never leaves a half-written photo that a later
a skip-existing run would mistake for finished work. Concurrency is bounded (default 4) and
transient failures back off and retry.

### Sizes in the manifest

The manifest records the size **on disk**, not the number of bytes Seesaw served. EXIF
stamping happens after the download and grows the file by a few hundred bytes, so recording
the served length would make every later presence check miss and re-download the whole
library. The served length is kept separately as `source_size`, and `sha256` hashes the
served bytes so it identifies the source rather than our stamped copy.

Skipping what is already downloaded is the **default and has no flag of its own**: `--all`
(and its inverse `--no-all`) is the single, unambiguous way to control it. An earlier
`--no-skip-existing` was removed because it was an exact synonym for `--all`, and two
spellings of one behaviour is a bug waiting to happen.

The manifest is a convenience, not the truth: a file the user deleted is not present just
because the index says so, and a file already sitting at the expected path (a restored
backup, a lost manifest) is treated as present. `--all` ignores presence entirely.

## Distribution

No Docker image. Sign-in needs a browser plus a human to solve a reCAPTCHA, and a
container can offer neither, so an image would ship a tool whose first command cannot run
inside it. The deliverable is the Python package plus `playwright install chromium`.

The one containerisable slice is `download`, which needs no browser and no password: run
`login` on a host and mount `session.json` read-only. Not built, and not planned.

## Secrets

The password comes from a flag, env var or `.env` and is registered with the reporter so it
can never be printed. The session cache holds cookies and `_xsrf`; it is written atomically
with `0600` permissions, and is gitignored along with `.env` and the output directory.
