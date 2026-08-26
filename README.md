# seesaw-media-downloader

Download photos, videos and PDFs from your **Seesaw family feed** to a local folder —
originals, not the lossy PDF/thumbnail export the web app offers.

> For family members downloading their own children's content. Seesaw has no public API,
> so this tool signs in with your own credentials and uses the same private endpoints the
> web app does. Use it at your own risk, and keep the default rate limits.

## Status

Work in progress. See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and the
slice-by-slice plan.

- [x] Config resolution (flag → env → `.env` → default) and login
- [x] `--list-only`
- [ ] `--since`
- [ ] `--all`
- [ ] `--skip-existing`
- [ ] Docker packaging

## Install

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run playwright install chromium
```

## Usage

Sign in once — this is the **only** command that opens a browser:

```bash
seesaw-dl login
```

Seesaw protects family sign-in with a reCAPTCHA, so the browser opens with your
credentials prefilled and you complete the challenge yourself. The session is then cached
(`~/.config/seesaw-dl/session.json`, `0600`) and reused indefinitely — until Seesaw
rejects it, at which point you are told to run `login` again.

Everything after that is non-interactive and needs no credentials at all:

```bash
seesaw-dl download --list-only
```

```bash
seesaw-dl download --list-only --json
```

Configuration comes from a CLI flag, an environment variable, a `.env` file, or a default —
in that order. See `.env.example`.

| Input | Flag | Env var | Required? | Default |
|---|---|---|---|---|
| Email | `--email` | `SEESAW_EMAIL` | `login` only | — |
| Password | `--password` | `SEESAW_PASSWORD` | `login` only | — |
| Output dir | `--out` | `SEESAW_OUTPUT_DIR` | unless `--list-only` | — |
| List only | `--list-only` | `SEESAW_LIST_ONLY` | no | `false` |
| Download all | `--all` | `SEESAW_DOWNLOAD_ALL` | no | `false` |
| Skip existing | `--skip-existing/--no-skip-existing` | `SEESAW_SKIP_EXISTING` | no | `true` |
| Since | `--since` | `SEESAW_SINCE` | no | unset |
| Concurrency | `--concurrency` | `SEESAW_CONCURRENCY` | no | `4` |
| Session cache | `--session-file` | `SEESAW_SESSION_FILE` | no | `~/.config/seesaw-dl/session.json` |
| JSON output | `--json` | `SEESAW_JSON` | no | `false` |
| Log level | `--log-level` | `SEESAW_LOG_LEVEL` | no | `info` |

Your password is never logged, and the session cache is written with `0600` permissions.
`download` never reads your password — only the cached session.

## What it does not do

Browsing Seesaw in a web browser marks posts as read. This tool only ever issues `GET`
requests and never calls `item/update_seen_state_v2`, so downloading leaves your unread
state alone.

## License

MIT — see [LICENSE](LICENSE).
