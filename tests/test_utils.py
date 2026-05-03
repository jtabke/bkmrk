"""Unit tests for bm.utils module."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from bm.utils import (
    _normalize_netloc_for_compare,
    _normalize_path_for_compare,
    _normalize_query_string,
    _parse_for_compare,
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

    def test_reject_all_dots_segment(self):
        """Should reject any segment that is entirely dots."""
        for s in ["...", "....", "a/.../b"]:
            with pytest.raises(SystemExit):
                _reject_unsafe(s)

    def test_reject_null_byte(self):
        """Should reject NUL byte in any segment."""
        with pytest.raises(SystemExit):
            _reject_unsafe("a/b\x00c")

    def test_accept_leading_dot_segment(self):
        """Single-dot prefix segments (e.g. .git) are accepted."""
        assert _reject_unsafe(".git/config") == ".git/config"

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

    def test_empty_url_normalizes_to_empty(self):
        """Empty URLs should produce an empty dedupe key."""
        assert normalize_url_for_compare("") == ""
        assert normalize_url_for_compare("   ") == ""

    def test_missing_scheme(self):
        """Should treat schemeless host paths as HTTP when a host is present."""
        assert normalize_url_for_compare("example.com/path") == "example.com/path"

    def test_non_host_schemeless_values_preserve_raw_lowercase(self):
        """Schemeless values without a host should use the raw text as their key."""
        assert normalize_url_for_compare("?A=1") == "?a=1"
        assert normalize_url_for_compare("/Local/Path") == "/local/path"

    def test_preserves_non_http_scheme(self):
        """Should keep non web schemes intact."""
        assert normalize_url_for_compare("mailto:user@example.com") == "mailto:user@example.com"
        assert normalize_url_for_compare("mailto:user@example.com?subject=Hi") == (
            "mailto:user@example.com?subject=Hi"
        )

    def test_web_path_params_are_preserved(self):
        """URL params should remain attached to the normalized web path."""
        result = normalize_url_for_compare("http://example.com/path;param?x=1")
        assert result == "example.com/path;param?x=1"

    def test_default_https_port_removed(self):
        """Should drop default HTTPS port."""
        result = normalize_url_for_compare("https://example.com:443/foo")
        assert result == "example.com/foo"

    def test_userinfo_is_lowered_and_preserved(self):
        """Userinfo should be split from host and normalized separately."""
        result = normalize_url_for_compare("https://User:Pass@www.Example.com:443/a")
        assert result == "user:pass@example.com/a"

    def test_multiple_at_signs_split_on_last_at(self):
        """Only the last @ separates userinfo from host."""
        result = normalize_url_for_compare("https://one@two@example.com/a")
        assert result == "one@two@example.com/a"

    def test_non_default_web_ports_are_preserved(self):
        """Only scheme-appropriate default ports should be removed."""
        assert normalize_url_for_compare("https://example.com:80/a") == "example.com:80/a"
        assert normalize_url_for_compare("http://example.com:443/a") == "example.com:443/a"
        assert normalize_url_for_compare("ftp://example.com:443/a") == "ftp://example.com:443/a"

    def test_invalid_port_keeps_raw_host_port(self):
        """Unparseable ports should not drop the host/port text."""
        result = normalize_url_for_compare("http://example.com:notaport/a")
        assert result == "example.com:notaport/a"


class TestUrlCompareHelpers:
    """Direct tests for URL normalization helpers targeted by mutation testing."""

    def test_normalize_netloc_empty_and_strips_whitespace(self):
        assert _normalize_netloc_for_compare("http", "") == ""
        assert _normalize_netloc_for_compare("http", "  EXAMPLE.com  ") == "example.com"

    def test_normalize_netloc_userinfo_and_ports(self):
        assert _normalize_netloc_for_compare("https", "User@www.Example.com:443") == (
            "user@example.com"
        )
        assert _normalize_netloc_for_compare("http", "example.com:80") == "example.com"
        assert _normalize_netloc_for_compare("", "example.com:80") == "example.com"
        assert _normalize_netloc_for_compare("https", "example.com:80") == "example.com:80"
        assert _normalize_netloc_for_compare("http", "example.com:foo:80") == "example.com:foo"
        assert _normalize_netloc_for_compare("http", "one@www.example.com@Host.com") == (
            "one@www.example.com@host.com"
        )

    def test_normalize_path_for_compare(self):
        assert _normalize_path_for_compare("") == ""
        assert _normalize_path_for_compare("foo//bar/../baz/") == "/foo/baz"
        assert _normalize_path_for_compare("XX/XXfoo") == "/XX/XXfoo"
        assert _normalize_path_for_compare("/") == ""
        assert _normalize_path_for_compare(".") == ""

    def test_parse_for_compare(self):
        parsed, scheme = _parse_for_compare("example.com/path")
        assert parsed.netloc == "example.com"
        assert scheme == "http"

        parsed, scheme = _parse_for_compare("MAILTO:User@Example.com")
        assert parsed.scheme == "mailto"
        assert scheme == "mailto"

        parsed, scheme = _parse_for_compare("http://")
        assert parsed.scheme == "http"
        assert scheme == "http"

        parsed, scheme = _parse_for_compare("://bad")
        assert parsed.path == "://bad"
        assert scheme == ""

        parsed, scheme = _parse_for_compare("")
        assert parsed.path == ""
        assert scheme == ""

    def test_normalize_query_string_keeps_blanks_and_sorts(self):
        assert _normalize_query_string("") == ""
        assert _normalize_query_string("b=&a=1") == "a=1&b="
        assert _normalize_query_string("&&") == ""


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
        assert slug.startswith("example-com-")
        assert "xxxx" not in slug

    def test_url_with_path(self):
        """Should include the final path segment in slug."""
        slug = create_slug_from_url("https://example.com/path/to/page")
        assert slug.startswith("example-com-page-")

    def test_nested_path_uses_only_final_segment(self):
        """Intermediate path segments should not be embedded in generated slugs."""
        slug = create_slug_from_url("https://example.com/foo/bar")
        assert slug.startswith("example-com-bar-")
        assert "/" not in slug

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

    def test_slug_is_lowercase(self):
        """Host portion of the slug must be lowercase."""
        slug = create_slug_from_url("https://EXAMPLE.com/Path")
        # Hash suffix is hex (lowercase). Whole slug should match the lower form.
        assert slug == slug.lower()

    def test_www_prefix_stripped(self):
        """`www.` prefix should not appear in the slug."""
        slug = create_slug_from_url("https://www.example.com")
        assert slug.startswith("example-com-")
        assert "www-" not in slug

    def test_no_netloc_falls_back_to_link(self):
        """Schemeless / no-host URLs should fall back to the literal `link`."""
        slug = create_slug_from_url("just-some-text")
        assert slug.startswith("link-") or slug.startswith("link")

    def test_path_segment_split_on_slash_only(self):
        """Path segmentation must split on '/' specifically (not whitespace)."""
        # Whitespace in the path should remain inside the last segment, then be
        # slug-normalized to a dash. If split(None) is used, only "bar" remains.
        slug = create_slug_from_url("https://example.com/foo bar")
        assert slug.startswith("example-com-foo-bar-")

    def test_path_trailing_slash_uses_last_non_empty_segment(self):
        """Trailing slashes should be stripped before selecting the final path segment."""
        slug = create_slug_from_url("https://example.com/foo/")
        assert slug.startswith("example-com-foo-")

    def test_path_strip_preserves_non_slash_edge_characters(self):
        """Only slashes should be stripped from path edges before segment selection."""
        slug = create_slug_from_url("https://example.com/XrayX/")
        assert slug.startswith("example-com-xrayx-")

    def test_colon_in_host_port_becomes_segment_dash(self):
        """Host ports should keep a dash separator rather than being concatenated."""
        slug = create_slug_from_url("https://example.com:8443/path")
        assert slug.startswith("example-com-8443-path-")


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
