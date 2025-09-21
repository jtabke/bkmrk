"""Unit tests for bm.models module."""

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
            notes="Some notes"
        )
        meta = bm.to_meta()
        expected = {
            "url": "https://example.com",
            "title": "Example",
            "tags": ["tag1", "tag2"],
            "created": "2023-01-01",
            "modified": "2023-01-02",
            "notes": "Some notes"
        }
        assert meta == expected

    def test_to_meta_filter_empty(self):
        """Should filter out empty values."""
        bm = Bookmark(url="https://example.com", title="", tags=[])
        meta = bm.to_meta()
        assert meta == {"url": "https://example.com"}