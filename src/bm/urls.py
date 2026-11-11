"""URL inspection, separate from identity, slugging, and dedupe normalization."""

from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_URL_SCHEMES = frozenset({"http", "https", "ftp", "ftps", "mailto"})


@dataclass(frozen=True)
class UrlInspection:
    """Structural diagnostics; callers decide whether to reject or preserve a URL."""

    scheme: str
    supported: bool
    problem: str | None = None


def inspect_url(url: str, schemes: frozenset[str] = ALLOWED_URL_SCHEMES) -> UrlInspection:
    """Inspect without fetching, opening, normalizing, or changing the saved URL."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return UrlInspection("", False, str(exc))
    scheme = parsed.scheme.lower()
    supported = scheme in schemes
    if not supported:
        return UrlInspection(scheme, False)
    try:
        if scheme == "mailto":
            if not parsed.path:
                raise ValueError("missing mail address")
        else:
            if not parsed.hostname:
                raise ValueError("missing URL host")
            _ = parsed.port
    except ValueError as exc:
        return UrlInspection(scheme, True, str(exc))
    return UrlInspection(scheme, True)
