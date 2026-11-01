"""Shared exception types. Messages are safe to show to the user (never contain secrets)."""
from __future__ import annotations

from typing import Optional


class BridgeError(Exception):
    """Base class; str(e) is always safe to send to Saved Messages."""


class InvalidInput(BridgeError):
    pass


class GitHubError(BridgeError):
    pass


class NotFound(GitHubError):
    pass


class RateLimited(GitHubError):
    def __init__(self, message: str, reset_at: Optional[float] = None):
        super().__init__(message)
        self.reset_at = reset_at


class TransportError(BridgeError):
    """Network-level failure (timeout, connection reset...)."""


class UnsafeURL(BridgeError):
    """A URL that is not an allowed GitHub host (SSRF protection)."""


class JobCancelled(BridgeError):
    pass


class DownloadError(BridgeError):
    pass


class IntegrityError(DownloadError):
    pass


class TooLarge(DownloadError):
    pass


class QuotaExceeded(DownloadError):
    pass


class SendError(BridgeError):
    pass


class NotLoggedIn(BridgeError):
    pass
