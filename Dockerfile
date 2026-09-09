# Download-only image: `seesaw-dl login` needs a real browser and a human to solve a
# reCAPTCHA, so signing in stays on the host. Sessions are honoured by Seesaw for a long
# time, which is what makes an unattended container worthwhile at all.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /src

# The base install deliberately excludes the `login` extra (Playwright + Chromium), which
# is most of what the image would otherwise weigh.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .


FROM python:3.12-slim-bookworm

# Timezone decides the dated folder layout and the EXIF stamps, so the zone database has
# to be present for `TZ` to mean anything.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# `config.py` evaluates `Path.home()` at import time. Run as an arbitrary uid (a NAS
# typically has no 1000) and there is no /etc/passwd entry to resolve it, so without HOME
# the CLI dies before it can parse the argument that would have overridden the path.
ENV HOME=/tmp

# Container inputs are environment variables: WORKDIR holds no `.env` for
# pydantic-settings to read, so nothing silently overrides what compose passes in.
ENV SEESAW_SESSION_FILE=/session/session.json
ENV SEESAW_OUTPUT_DIR=/data

WORKDIR /data

# A default only -- `user:`/`--user` overrides it, and every path the image touches is
# world-readable, so any uid works. See README for NAS uid/gid.
USER 1000:1000

# `list` remains useful for a dry run; `login` is present but fails with a message
# pointing back to the host, since Playwright is not installed.
ENTRYPOINT ["seesaw-dl"]
CMD ["download"]
