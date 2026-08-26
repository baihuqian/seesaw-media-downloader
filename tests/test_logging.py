from __future__ import annotations

import pytest

from seesaw_dl.logging import REDACTED, clear_secrets, redact, register_secret


@pytest.fixture(autouse=True)
def _no_secrets():
    clear_secrets()
    yield
    clear_secrets()


def test_registered_secret_is_blanked() -> None:
    register_secret("hunter2000")
    assert redact("signing in with hunter2000") == f"signing in with {REDACTED}"


def test_short_values_are_not_registered() -> None:
    register_secret("ab")
    assert redact("ab cd") == "ab cd"


def test_xsrf_in_a_url_is_blanked() -> None:
    out = redact("GET https://app.seesaw.me/api/feed?_xsrf=abc123&limit=20")
    assert "abc123" not in out
    assert "limit=20" in out


def test_cookie_and_authorization_headers_are_blanked() -> None:
    assert "xyz" not in redact("Cookie: session=xyz")
    assert "deadbeef" not in redact("Authorization: Bearer deadbeef")


def test_legitimate_prose_is_untouched() -> None:
    message = "  - password: pass --password, or set SEESAW_PASSWORD in the environment or .env"
    assert redact(message) == message
