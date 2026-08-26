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

Sign-in is gated by reCAPTCHA (`POST /api/auth/login` → `{"recaptcha_required": true}`),
so `login` is interactive by necessity and every other command is not.

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
| `planner.py` | Walks every child and class into a `Plan`; the single source of truth for `--list-only` and downloads alike. |
| `render.py` | Table and newline-delimited JSON rendering of a plan. |
| `downloader.py` | *(slice 3)* Async downloads, atomic writes, sidecars. |
| — | Endpoint discovery turned out to be unnecessary: the endpoints are stable constants in `api.py`, and a change surfaces as `ApiContractError` rather than being silently guessed at. |
| `manifest.py` | *(slice 3+)* `manifest.json` index backing `--skip-existing`. |

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

## Secrets

The password comes from a flag, env var or `.env` and is registered with the reporter so it
can never be printed. The session cache holds cookies and `_xsrf`; it is written atomically
with `0600` permissions, and is gitignored along with `.env` and the output directory.
