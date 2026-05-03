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
