"""Unit tests for bm.utils module."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from bm.utils import (
    _reject_unsafe,
    create_slug_from_url,
    id_to_path,
    is_relative_to,
    iso_now,
    normalize_slug,
    normalize_url_for_compare,
    parse_iso,
    rid,
    to_epoch,
)


class TestIsoNow:
    """Test iso_now function."""

    def test_returns_string(self):
        """Should return a string."""
        result = iso_now()
        assert isinstance(result, str)

    def test_format(self):
        """Should be in ISO format with timezone."""
        result = iso_now()
        ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")
        assert ISO_RE.match(result)

    def test_iso_now_shape_and_parseable(self):
        """Should be parseable and close to now."""
        s = iso_now()
        dt = parse_iso(s)
        assert dt is not None
        assert abs(dt.timestamp() - datetime.now(dt.tzinfo).timestamp()) < 2.5


class TestParseIso:
    """Test parse_iso function."""

    def test_none_for_empty(self):
        """Should return None for empty string."""
        assert parse_iso("") is None
        assert parse_iso("   ") is None

    def test_date_only(self):
        """Should parse YYYY-MM-DD as start of day."""
        dt = parse_iso("2023-01-15")
        assert dt is not None
        assert dt.year == 2023
        assert dt.month == 1
        assert dt.day == 15
        assert dt.hour == 0
        assert dt.minute == 0

    def test_full_iso(self):
        """Should parse full ISO timestamp."""
        dt = parse_iso("2023-01-15T10:30:45+05:00")
        assert dt is not None
        assert dt.year == 2023
        assert dt.month == 1
        assert dt.day == 15
        assert dt.hour == 10
        assert dt.minute == 30
        assert dt.second == 45

    def test_z_suffix(self):
        """Should handle Z suffix."""
        dt = parse_iso("2023-01-15T10:30:45Z")
        assert dt is not None
        assert dt.year == 2023

    def test_invalid(self):
        """Should return None for invalid formats."""
        assert parse_iso("invalid") is None
        assert parse_iso("2023-13-45") is None


class TestToEpoch:
    """Test to_epoch function."""

    def test_none_for_none(self):
        """Should return None for None input."""
        assert to_epoch(None) is None

    def test_epoch_conversion(self):
        """Should convert datetime to epoch timestamp."""
        dt = datetime(2023, 1, 15, 21, 10, 45, tzinfo=timezone.utc)
        epoch = to_epoch(dt)
        assert epoch == 1673817045

    def test_to_epoch_offset(self):
        """Should handle offset datetimes."""
        offset = datetime(2023, 1, 15, 22, 10, 45, tzinfo=timezone(timedelta(hours=1)))
        assert to_epoch(offset) == 1673817045


class TestNormalizeSlug:
    """Test normalize_slug function."""

    def test_basic(self):
        """Should normalize basic strings."""
        assert normalize_slug("hello world") == "hello-world"
        assert normalize_slug("Hello/World") == "hello/world"

    def test_special_chars(self):
        """Should remove special characters."""
        assert normalize_slug("hello@world!") == "helloworld"

    def test_multiple_dashes(self):
        """Should collapse multiple dashes."""
        assert normalize_slug("hello--world") == "hello-world"

    def test_strip_slashes(self):
        """Should strip leading/trailing slashes."""
        assert normalize_slug("/hello/world/") == "hello/world"

    def test_empty(self):
        """Should return 'untitled' for empty string."""
        assert normalize_slug("") == "untitled"
        assert normalize_slug("   ") == "untitled"

    def test_trim_leading_trailing_slashes(self):
        """Should trim leading and trailing slashes."""
        assert normalize_slug("/hello/world/") == "hello/world"
        assert normalize_slug("///hello///") == "hello"

    def test_reject_dot_dot(self):
        """Should reject paths with .."""
        with pytest.raises(SystemExit):
            _reject_unsafe("../escape")

    def test_reject_absolute_path(self):
        """Should reject absolute paths."""
        with pytest.raises(SystemExit):
            _reject_unsafe("/absolute/path")

    def test_normalize_slug_collapse_multiple_dashes(self):
        """Should collapse multiple consecutive dashes."""
        assert normalize_slug("hello---world") == "hello-world"
        assert normalize_slug("a----b") == "a-b"

    def test_normalize_slug_unicode_handling(self):
        """Should handle unicode characters."""
        result = normalize_slug("héllo wörld")
        assert result == "héllo-wörld"

    def test_normalize_slug_strip_dashes_from_path_segments(self):
        """Should strip trailing dashes from each path segment."""
        assert normalize_slug("business-/slug") == "business/slug"
        assert normalize_slug("business-/slug-") == "business/slug"
        assert normalize_slug("-business/slug") == "business/slug"
        assert normalize_slug("business-/-slug") == "business/slug"


class TestNormalizeUrlForCompare:
    """Test normalize_url_for_compare function."""

    def test_basic_web_normalization(self):
        """Should ignore scheme diffs, www, default ports, and unordered query."""
        url1 = "HTTP://www.Example.com:80/foo//bar/?b=2&a=1#frag"
        url2 = "https://example.com/foo/bar?a=1&b=2"
        normalized = normalize_url_for_compare(url1)
        assert normalized == normalize_url_for_compare(url2)
        assert normalized == "example.com/foo/bar?a=1&b=2"

    def test_missing_scheme(self):
        """Should treat schemeless host paths as HTTP."""
        assert normalize_url_for_compare("example.com/path") == "example.com/path"

    def test_preserves_non_http_scheme(self):
        """Should keep non web schemes intact."""
        assert normalize_url_for_compare("mailto:user@example.com") == "mailto:user@example.com"

    def test_default_https_port_removed(self):
        """Should drop default HTTPS port."""
        result = normalize_url_for_compare("https://example.com:443/foo")
        assert result == "example.com/foo"


class TestRejectUnsafe:
    """Test _reject_unsafe function."""

    def test_safe_path(self):
        """Should return path for safe input."""
        assert _reject_unsafe("hello/world") == "hello/world"

    def test_dot_dot(self):
        """Should die for .. in path."""
        with pytest.raises(SystemExit):
            _reject_unsafe("hello/../world")

    def test_absolute(self):
        """Should die for absolute paths."""
        with pytest.raises(SystemExit):
            _reject_unsafe("/absolute/path")


class TestIsRelativeTo:
    """Test is_relative_to function."""

    def test_relative(self, tmp_path):
        """Should return True for relative paths."""
        base = tmp_path / "base"
        base.mkdir()
        child = base / "child"
        child.mkdir()
        assert is_relative_to(child, base)

    def test_not_relative(self, tmp_path):
        """Should return False for non-relative paths."""
        base = tmp_path / "base"
        base.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        assert not is_relative_to(other, base)


class TestIdToPath:
    """Test id_to_path function."""

    def test_basic(self, tmp_path):
        """Should create path with extension."""
        result = id_to_path(tmp_path, "test-slug")
        assert str(result) == str(tmp_path / "test-slug.bm")


class TestCreateSlugFromUrl:
    """Test create_slug_from_url function."""

    def test_basic_url(self):
        """Should create slug from URL."""
        slug = create_slug_from_url("https://example.com/path")
        assert "example-com" in slug
        assert re.search(r"-[0-9a-f]{7}$", slug)

    def test_no_path(self):
        """Should handle URL without path."""
        slug = create_slug_from_url("https://example.com")
        assert "example-com" in slug

    def test_url_with_path(self):
        """Should include path in slug."""
        slug = create_slug_from_url("https://example.com/path/to/page")
        assert "example-com" in slug
        assert "path" in slug or "page" in slug

    def test_unicode_url(self):
        """Should handle unicode characters in URL."""
        slug = create_slug_from_url("https://exämple.com/päth")
        # Should create a valid slug without crashing
        assert isinstance(slug, str)
        assert len(slug) > 0

    def test_url_with_query_params(self):
        """Should handle URLs with query parameters."""
        slug = create_slug_from_url("https://example.com/path?query=value")
        assert "example-com" in slug


class TestRid:
    """Test rid function."""

    def test_consistent(self):
        """Should return consistent hash for same URL."""
        url = "https://example.com"
        rid1 = rid(url)
        rid2 = rid(url)
        assert rid1 == rid2
        assert len(rid1) == 12  # 6 bytes * 2 hex chars

    def test_different_urls(self):
        """Should return different hashes for different URLs."""
        rid1 = rid("https://example.com")
        rid2 = rid("https://example.org")
        assert rid1 != rid2

    def test_rid_shape_and_hex(self):
        """Should be 12 hex chars."""
        h = rid("https://example.com")
        assert len(h) == 12
        assert re.fullmatch(r"[0-9a-f]{12}", h)
