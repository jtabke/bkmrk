"""Unit tests for bm.models module."""

import pytest

from bm.io import build_text, parse_front_matter
from bm.models import Bookmark


class TestBookmark:
    """Test Bookmark dataclass."""

    def test_init(self):
        """Should initialize with defaults."""
        bm = Bookmark(url="https://example.com")
        assert bm.url == "https://example.com"
        assert bm.title == ""
        assert bm.tags == []
        assert bm.created is None
        assert bm.modified is None
        assert bm.notes == ""

    def test_to_meta(self):
        """Should convert to metadata dict."""
        bm = Bookmark(
            url="https://example.com",
            title="Example",
            tags=["tag1", "tag2"],
            created="2023-01-01",
            modified="2023-01-02",
            notes="Some notes",
        )
        meta = bm.to_meta()
        expected = {
            "url": "https://example.com",
            "title": "Example",
            "tags": ["tag1", "tag2"],
            "created": "2023-01-01",
            "modified": "2023-01-02",
            "notes": "Some notes",
        }
        assert meta == expected

    def test_to_meta_filter_empty(self):
        """Should filter out empty values."""
        bm = Bookmark(url="https://example.com", title="", tags=[])
        meta = bm.to_meta()
        assert meta == {"url": "https://example.com"}

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            (dict(url="https://e", title="", tags=[]), {"url": "https://e"}),
            (
                dict(url="https://e", title="T", tags=["x"]),
                {"url": "https://e", "title": "T", "tags": ["x"]},
            ),
        ],
    )
    def test_to_meta_filter_matrix(self, kwargs, expected):
        """Should filter empty values in various combinations."""
        bm = Bookmark(**kwargs)
        assert bm.to_meta() == expected

    def test_round_trip_meta(self):
        """Should round-trip through I/O."""
        bm = Bookmark(url="https://e", title="T", tags=["x"], notes="N")
        txt = build_text(bm.to_meta(), bm.notes)
        meta2, body2 = parse_front_matter(txt)
        assert meta2["url"] == "https://e"
        assert meta2["title"] == "T"
        assert meta2["tags"] == ["x"]
        assert body2 == "N"
