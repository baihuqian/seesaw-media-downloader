"""Exception types shared across the package."""

from __future__ import annotations


class SeesawError(Exception):
    """Base class for every error this tool raises deliberately."""


class ConfigError(SeesawError):
    """A required input was missing or an input was invalid."""


class AuthError(SeesawError):
    """Login failed, or a cached session could not be refreshed."""


class ApiContractError(SeesawError):
    """Seesaw's private API returned something we do not know how to read.

    This almost always means Seesaw changed their web app. The message should tell
    the user what we expected, what we got, and that re-running discovery may help.
    """


class DownloadError(SeesawError):
    """A media asset could not be fetched or verified."""
