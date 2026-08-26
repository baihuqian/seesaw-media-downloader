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

### What we know about Seesaw's private API

- Same-origin JSON API under `https://app.seesaw.me/api/...`
  (observed live: `/api/app/features`, `/api/app/location_data`).
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
| `discovery.py` | *(slice 1)* Captures the family-feed endpoint from real traffic. |
| `api.py` / `models.py` | *(slice 1)* Authenticated HTTP client and feed models. |
| `planner.py` | *(slice 1+)* Turns mode flags + manifest into a download plan. |
| `downloader.py` | *(slice 3)* Async downloads, atomic writes, sidecars. |
| `manifest.py` | *(slice 3+)* `manifest.json` index backing `--skip-existing`. |

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
