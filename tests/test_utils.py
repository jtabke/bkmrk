"""Unit tests for bm.utils module."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from bm.utils import (
    iso_now, parse_iso, to_epoch, normalize_slug, _reject_unsafe,
    is_relative_to, id_to_path, create_slug_from_url, rid
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
        # Should match YYYY-MM-DDTHH:MM:SS±HH:MM
        assert len(result) >= 19  # minimum length
        assert 'T' in result
        assert '+' in result or '-' in result


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
        dt = datetime(2023, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        epoch = to_epoch(dt)
        assert epoch == 1673776245


class TestNormalizeSlug:
    """Test normalize_slug function."""

    def test_basic(self):
        """Should normalize basic strings."""
        assert normalize_slug("hello world") == "hello-world"
        assert normalize_slug("Hello/World") == "hello/world"

    def test_special_chars(self):
        """Should replace special characters with dashes."""
        assert normalize_slug("hello@world!") == "hello-world-"

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
        assert slug.endswith("-" + "d" * 7)  # short hash

    def test_no_path(self):
        """Should handle URL without path."""
        slug = create_slug_from_url("https://example.com")
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