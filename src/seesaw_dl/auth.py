"""Login and session persistence.

Playwright is used only to sign in: it drives the real web app once, then we export the
browser's storage state (cookies + local storage) and the ``_xsrf`` token. Everything
after login runs over plain HTTP with that material, which is much faster and far less
brittle than driving the DOM.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import AuthError
from .logging import Reporter, register_secret

# The role in the URL is "parent", even though the UI calls it "Family Member".
LOGIN_URL = "https://app.seesaw.me/#/login?role=parent"
ROLE_PICKER_URL = "https://app.seesaw.me/"
FEED_URL = "https://app.seesaw.me/#/family/feed"
ORIGIN = "https://app.seesaw.me"

# Sessions are kept for as long as Seesaw honours them. Signing in needs a human (see
# RECAPTCHA_MESSAGE), so guessing at an expiry and throwing a working session away would
# force needless manual logins. `download` reuses whatever is cached and only reports a
# problem when the API itself rejects it.

RECAPTCHA_MESSAGE = (
    "Seesaw answered the sign-in with a reCAPTCHA challenge, so it cannot be completed "
    "automatically.\n"
    "Run `seesaw-dl login --headful`: a browser window opens with your email and password "
    "already filled in, you solve the challenge and sign in yourself, and the session is "
    "then cached so later runs need no browser at all."
)


@dataclass
class Session:
    """Everything needed to talk to Seesaw's private API without a browser."""

    storage_state: dict[str, Any]
    xsrf: str
    email: str
    created_at: float = field(default_factory=time.time)
    release: str | None = None
    #: Every ``/api/`` URL the web app called during login. Seeds endpoint discovery.
    observed_api: list[str] = field(default_factory=list)

    @property
    def cookies(self) -> dict[str, str]:
        return {
            cookie["name"]: cookie["value"]
            for cookie in self.storage_state.get("cookies", [])
            if cookie.get("domain", "").endswith("seesaw.me")
        }

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_at)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "email": self.email,
            "created_at": self.created_at,
            "xsrf": self.xsrf,
            "release": self.release,
            "observed_api": self.observed_api,
            "storage_state": self.storage_state,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Session:
        try:
            return cls(
                storage_state=data["storage_state"],
                xsrf=data["xsrf"],
                email=data["email"],
                created_at=float(data.get("created_at", 0.0)),
                release=data.get("release"),
                observed_api=list(data.get("observed_api", [])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError(f"Session cache is malformed: {exc}") from exc


class SessionStore:
    """Reads and writes the on-disk session cache with ``0600`` permissions."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, email: str | None = None) -> Session | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            session = Session.from_json(data)
        except AuthError:
            return None
        if email is not None and session.email != email:
            return None
        return session

    def save(self, session: Session) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(session.to_json()), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _extract_xsrf(cookies: dict[str, str]) -> str:
    for name in ("_xsrf", "xsrf", "XSRF-TOKEN"):
        if name in cookies:
            return cookies[name]
    raise AuthError(
        "Signed in, but no _xsrf cookie was found. Seesaw may have changed its login flow; "
        "re-run with --log-level debug to see what the browser received."
    )


def login_with_playwright(
    email: str,
    password: str,
    reporter: Reporter,
    headful: bool = False,
    timeout_ms: int = 60_000,
) -> Session:
    """Sign in as a family member and return a reusable :class:`Session`.

    With ``headful=True`` the browser is shown and we simply wait for the user to finish
    signing in themselves -- the escape hatch for CAPTCHAs, SSO or 2FA.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - import guard
        raise AuthError(
            "Playwright is not installed, so `login` is unavailable. Install the "
            "optional 'login' extra, then: playwright install chromium\n"
            "(The Docker image omits it deliberately: sign in on the host and mount the "
            "session file into the container.)"
        ) from exc

    release: str | None = None
    observed: list[str] = []
    recaptcha_required = False

    if headful:
        # A person has to read, click and possibly solve a challenge.
        timeout_ms = max(timeout_ms, 5 * 60_000)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headful)
        context = browser.new_context()
        page = context.new_page()

        def _observe(request: Any) -> None:
            nonlocal release
            url = request.url
            if "/api/" not in url:
                return
            if release is None and "_release=" in url:
                release = url.split("_release=")[1].split("&")[0]
            if url not in observed:
                observed.append(url)

        def _watch_login(response: Any) -> None:
            nonlocal recaptcha_required
            if "/api/auth/login" not in response.url:
                return
            try:
                body = response.json()
            except Exception:  # noqa: BLE001 - a non-JSON body tells us nothing
                return
            payload = body.get("response") or {}
            if payload.get("recaptcha_required"):
                recaptcha_required = True
                reporter.debug("login response asked for a reCAPTCHA")

        page.on("request", _observe)
        page.on("response", _watch_login)

        reporter.debug(f"opening {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=timeout_ms)

        if headful:
            # Prefill what we can; the human only needs to solve the challenge and submit.
            try:
                _fill_login_form(page, email, password, reporter, timeout_ms, submit=False)
            except AuthError as exc:
                reporter.debug(f"could not prefill the form: {exc}")
            reporter.info(
                "A browser window is open with your credentials filled in. "
                "Complete the sign-in (including any reCAPTCHA) -- waiting for the feed..."
            )
        else:
            _fill_login_form(page, email, password, reporter, timeout_ms)

        try:
            # Seesaw routes families to a hash route under #/family/ (and has used
            # #/parent/ historically), so accept either.
            page.wait_for_url(
                lambda url: "/family/" in url or "/parent/" in url.split("#", 1)[-1],
                timeout=timeout_ms,
            )
        except PWTimeout as exc:
            _dump_state(page, reporter)
            browser.close()
            if recaptcha_required:
                raise AuthError(RECAPTCHA_MESSAGE) from exc
            raise AuthError(
                "Sign-in did not reach the family feed. Check the email and password, or "
                "re-run `seesaw-dl login --headful` to sign in manually."
            ) from exc

        state = context.storage_state()
        browser.close()

    cookies = {
        c["name"]: c["value"]
        for c in state.get("cookies", [])
        if c.get("domain", "").endswith("seesaw.me")
    }
    session = Session(
        storage_state=dict(state),
        xsrf=_extract_xsrf(cookies),
        email=email,
        release=release,
        observed_api=observed,
    )
    _register(session)
    reporter.debug(
        f"captured session (release={release or 'unknown'}, {len(observed)} api calls seen)"
    )
    return session


def _fill_login_form(
    page: Any,
    email: str,
    password: str,
    reporter: Reporter,
    timeout_ms: int,
    submit: bool = True,
) -> None:
    """Fill the family email/password form.

    Selectors are kept broad on purpose: Seesaw ships UI changes often, and a slightly
    fuzzy selector is cheaper to maintain than an exact one.
    """
    from playwright.sync_api import TimeoutError as PWTimeout

    email_selectors = [
        "#sign_in_email",
        "input[type='email']",
        "input[name='email']",
        "input[placeholder*='mail' i]",
    ]
    password_selectors = [
        "#sign_in_password",
        "input[type='password']",
        "input[name='password']",
    ]

    email_box = _first_visible(page, email_selectors, timeout_ms)
    if email_box is None:
        # Deep-linking to the role can be ignored on a cold load, which drops us on the
        # role picker instead. Pick "I'm a Family Member" and look again.
        reporter.debug("no email field yet; trying the role picker")
        try:
            page.get_by_role("button", name="Family Member").click(timeout=10_000)
            page.wait_for_timeout(1_000)
        except Exception as exc:  # noqa: BLE001 - any failure here means the same thing
            reporter.debug(f"role picker not usable: {exc}")
        email_box = _first_visible(page, email_selectors, timeout_ms)
    if email_box is None:
        raise AuthError(
            "Could not find the email field on the Seesaw sign-in page. "
            "Run `seesaw-dl login --headful` to sign in manually."
        )
    email_box.fill(email)

    password_box = _first_visible(page, password_selectors, timeout_ms)
    if password_box is None:
        raise AuthError(
            "Could not find the password field on the Seesaw sign-in page. "
            "Run `seesaw-dl login --headful` to sign in manually."
        )
    password_box.fill(password)
    if not submit:
        return

    reporter.debug("submitting sign-in form")
    button = page.get_by_role("button", name="Family Member Sign In").first
    try:
        button.click(timeout=10_000)
    except PWTimeout:  # pragma: no cover - defensive
        password_box.press("Enter")


def _first_visible(page: Any, selectors: list[str], timeout_ms: int) -> Any | None:
    from playwright.sync_api import TimeoutError as PWTimeout

    per_selector = max(2_000, timeout_ms // (len(selectors) * 2))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=per_selector)
            return locator
        except PWTimeout:
            continue
    return None


def _dump_state(page: Any, reporter: Reporter) -> None:
    try:
        reporter.debug(f"stuck at url={page.url}")
        text = page.inner_text("body")[:500]
        reporter.debug(f"page text: {text!r}")
    except Exception:  # pragma: no cover - best effort only
        pass


def get_session(
    email: str,
    password: str,
    store: SessionStore,
    reporter: Reporter,
    force: bool = False,
    headful: bool = False,
) -> Session:
    """Return a session for the ``login`` command, reusing the cache unless forced.

    This is the only path that may open a browser.
    """
    if not force:
        cached = store.load(email)
        if cached is not None:
            _register(cached)
            reporter.debug(
                f"reusing cached session from {store.path} "
                f"(age {cached.age_seconds / 3600:.1f}h)"
            )
            return cached
    session = login_with_playwright(email, password, reporter, headful=headful)
    store.save(session)
    reporter.debug(f"session cached at {store.path} (0600)")
    return session


NO_SESSION_MESSAGE = (
    "No cached Seesaw session was found.\n"
    "Run `seesaw-dl login` first -- it opens a browser once, you sign in, and the session "
    "is reused by every later run."
)

SESSION_REJECTED_MESSAGE = (
    "Seesaw rejected the cached session, so it has expired or been revoked.\n"
    "Run `seesaw-dl login` again to refresh it."
)


def load_session(store: SessionStore, reporter: Reporter) -> Session:
    """Load the cached session for a non-interactive command.

    Never opens a browser: signing in requires a human, so commands that download simply
    report what the user needs to run.
    """
    session = store.load()
    if session is None:
        raise AuthError(NO_SESSION_MESSAGE)
    _register(session)
    reporter.debug(
        f"using cached session for {session.email} from {store.path} "
        f"(age {session.age_seconds / 3600:.1f}h)"
    )
    return session


def _register(session: Session) -> None:
    register_secret(session.xsrf)
    for value in session.cookies.values():
        register_secret(value)
