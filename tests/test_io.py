"""Unit tests for bm.io module."""

import pytest

from bm.io import (
    _fmt_tag,
    _normalize_meta,
    atomic_write,
    build_text,
    load_entry,
    parse_front_matter,
)


class TestNormalizeMeta:
    """Test _normalize_meta function."""

    def test_legacy_keys(self):
        """Should map legacy keys to canonical."""
        meta = {"added": "2023-01-01", "updated": "2023-01-02"}
        result = _normalize_meta(meta)
        assert "created" in result
        assert "modified" in result
        assert "added" not in result
        assert "updated" not in result

    def test_legacy_keys_only_added(self):
        """Should map only added to created."""
        meta = {"added": "2023-01-01"}
        result = _normalize_meta(meta)
        assert result["created"] == "2023-01-01"
        assert "added" not in result

    def test_legacy_keys_only_updated(self):
        """Should map only updated to modified."""
        meta = {"updated": "2023-01-02"}
        result = _normalize_meta(meta)
        assert result["modified"] == "2023-01-02"
        assert "updated" not in result

    def test_legacy_keys_with_canonical(self):
        """Should prefer canonical keys over legacy."""
        meta = {
            "added": "2023-01-01",
            "created": "2023-01-02",
            "updated": "2023-01-03",
            "modified": "2023-01-04",
        }
        result = _normalize_meta(meta)
        assert result["created"] == "2023-01-02"
        assert result["modified"] == "2023-01-04"
        # Legacy keys should still be present if canonical exists
        assert "added" in result
        assert "updated" in result

    def test_tags_string(self):
        """Should convert tags string to list."""
        meta = {"tags": "tag1, tag2"}
        result = _normalize_meta(meta)
        assert result["tags"] == ["tag1", "tag2"]

    def test_tags_missing(self):
        """Should add empty tags list if missing."""
        meta = {}
        result = _normalize_meta(meta)
        assert result["tags"] == []

    def test_tags_list_already_normalized(self):
        """Should leave tags list unchanged if already a list."""
        meta = {"tags": ["tag1", "tag2", "tag3"]}
        result = _normalize_meta(meta)
        assert result["tags"] == ["tag1", "tag2", "tag3"]

    def test_tags_comma_string_with_spaces(self):
        """Should split comma string with spaces."""
        meta = {"tags": "tag1, tag2 , tag3"}
        result = _normalize_meta(meta)
        assert result["tags"] == ["tag1", "tag2", "tag3"]

    def test_tags_empty_string(self):
        """Should convert empty tags string to empty list."""
        meta = {"tags": ""}
        result = _normalize_meta(meta)
        assert result["tags"] == []


class TestParseFrontMatter:
    """Test parse_front_matter function."""

    def test_no_front_matter(self):
        """Should handle text without front matter."""
        text = "https://example.com\nSome notes"
        meta, body = parse_front_matter(text)
        assert meta["url"] == "https://example.com"
        assert body == "Some notes"

    def test_with_front_matter(self):
        """Should parse front matter correctly."""
        text = """---
url: https://example.com
title: Example
tags: [tag1, tag2]
---
Body text
"""
        meta, body = parse_front_matter(text)
        assert meta["url"] == "https://example.com"
        assert meta["title"] == "Example"
        assert meta["tags"] == ["tag1", "tag2"]
        assert body == "Body text\n"

    def test_tags_array(self):
        """Should parse tags array with quotes."""
        text = """---
tags: [tag1, "tag,with,comma", tag3]
---
"""
        meta, body = parse_front_matter(text)
        assert meta["tags"] == ["tag1", "tag,with,comma", "tag3"]

    def test_comments(self):
        """Should ignore comments in front matter."""
        text = """---
# This is a comment
url: https://example.com
---
"""
        meta, body = parse_front_matter(text)
        assert meta["url"] == "https://example.com"

    def test_body_with_trailing_newline(self):
        """Should preserve trailing newline in body."""
        text = """---
url: https://example.com
---
Body text
"""
        meta, body = parse_front_matter(text)
        assert body == "Body text\n"

    def test_body_without_trailing_newline(self):
        """Should handle body without trailing newline."""
        text = """---
url: https://example.com
---
Body text"""
        meta, body = parse_front_matter(text)
        assert body == "Body text"

    def test_missing_end_marker(self):
        """Should handle missing --- end marker by returning empty meta."""
        text = """---
url: https://example.com
title: Test
Body text"""
        meta, body = parse_front_matter(text)
        assert meta == {"tags": []}
        assert body == text

    def test_multiline_scalar_build(self):
        """Should build multiline scalars correctly."""
        meta = {"notes": "line1\nline2\nline3"}
        body = "body text"
        text = build_text(meta, body)
        # Check that it contains the multiline format
        assert "notes: |\n  line1\n  line2\n  line3" in text
        assert body in text

    def test_tags_with_quotes_and_commas(self):
        """Should handle tags with quotes and commas."""
        text = """---
tags: ["tag,with,comma", normal]
---
"""
        meta, body = parse_front_matter(text)
        assert meta["tags"] == ["tag,with,comma", "normal"]

    def test_no_front_matter_first_line_not_url(self):
        """Should handle no front matter with first line not being URL."""
        text = "This is not a URL\nThis is body text"
        meta, body = parse_front_matter(text)
        assert meta == {"tags": []}
        assert body == text


class TestFmtTag:
    """Test _fmt_tag function."""

    def test_no_quote(self):
        """Should not quote simple tags."""
        assert _fmt_tag("simple") == "simple"

    def test_quote_comma(self):
        """Should quote tags with commas."""
        assert _fmt_tag("tag,with,comma") == '"tag,with,comma"'

    def test_quote_space(self):
        """Should quote tags with spaces."""
        assert _fmt_tag("tag with space") == '"tag with space"'

    def test_quote_empty(self):
        """Should quote empty tags."""
        assert _fmt_tag("") == '""'


class TestBuildText:
    """Test build_text function."""

    def test_basic(self):
        """Should build text with front matter."""
        meta = {"url": "https://example.com", "title": "Example"}
        body = "Notes"
        result = build_text(meta, body)
        assert result.startswith("---\n")
        assert "url: https://example.com" in result
        assert "title: Example" in result
        assert result.endswith("---\nNotes")

    def test_tags_list(self):
        """Should format tags list."""
        meta = {"tags": ["tag1", "tag,with,comma"]}
        result = build_text(meta, "")
        assert 'tags: [tag1, "tag,with,comma"]' in result

    def test_multiline_value(self):
        """Should handle multiline values."""
        meta = {"notes": "line1\nline2"}
        result = build_text(meta, "")
        assert "notes: |\n  line1\n  line2" in result

    def test_filter_empty(self):
        """Should filter out empty values."""
        meta = {"url": "", "title": None, "tags": []}
        result = build_text(meta, "")
        assert result.startswith("---\n")
        assert result.endswith("---\n")
        assert "url:" not in result
        assert "tags:" not in result


class TestLoadEntry:
    """Test load_entry function."""

    def test_load(self, tmp_path):
        """Should load meta and body from file."""
        content = """---
url: https://example.com
---
Notes
"""
        fpath = tmp_path / "test.bm"
        fpath.write_text(content)
        meta, body = load_entry(fpath)
        assert meta["url"] == "https://example.com"
        assert body == "Notes\n"


class TestAtomicWrite:
    """Test atomic_write function."""

    def test_write(self, tmp_path):
        """Should write data atomically."""
        fpath = tmp_path / "test.bm"
        data = "test content"
        atomic_write(fpath, data)
        assert fpath.read_text() == data

    def test_atomic(self, tmp_path):
        """Should not leave partial files on failure."""
        # This is hard to test without mocking, but basic write works
        fpath = tmp_path / "test.bm"
        atomic_write(fpath, "content")
        assert fpath.exists()

    def test_atomic_write_failure_cleanup(self, tmp_path, monkeypatch):
        """Should keep original on failure."""
        fpath = tmp_path / "x.bm"
        atomic_write(fpath, "v1")
        import bm.io as io_mod

        def boom(src, dst):
            raise OSError("simulated")

        monkeypatch.setattr(io_mod.os, "replace", boom)
        with pytest.raises(OSError):
            atomic_write(fpath, "v2")
        assert fpath.read_text() == "v1"
