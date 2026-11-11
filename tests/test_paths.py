"""Bookmark path syntax and containment contracts."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from bm.errors import UnsafePathError
from bm.paths import id_to_path, is_relative_to, is_reserved_path, normalize_slug, reject_unsafe


@given(
    value=st.one_of(
        st.text(max_size=80),
        st.text(alphabet="abAB01-/._ ", max_size=80),
    )
)
@example(value="a/-/b")
@example(value="-/-/a")
def test_normalize_slug_is_idempotent(value):
    normalized = normalize_slug(value)
    assert normalize_slug(normalized) == normalized


@given(
    slug=st.one_of(
        st.text(max_size=60),
        st.text(alphabet="abcABC012-._/\\\\ :~", max_size=60),
    )
)
def test_generated_bookmark_paths_stay_inside_store(slug):
    # Each generated example gets its own root; no shared pytest fixture state.
    with TemporaryDirectory() as directory:
        root = Path(directory) / "store"
        try:
            path = id_to_path(root, slug)
        except UnsafePathError:
            return
        relative = path.resolve().relative_to(root.resolve())
        assert relative.parts
        assert relative.suffix == ".bm"
        assert all(part.casefold() != ".git" for part in relative.parts)
        assert not root.exists()


@pytest.mark.parametrize(
    "name", [r".git\config", r"nested\.GIT\hooks\entry", ".git/config", "nested/.GIT/hooks"]
)
def test_reserved_namespace_detection_handles_both_separators(name):
    assert is_reserved_path(name)


class TestNormalizeSlug:
    """Test normalize_slug function."""

    def test_basic(self):
        """Should normalize basic strings."""
        assert normalize_slug("hello world") == "hello-world"
        assert normalize_slug("Hello/World") == "hello/world"
        assert normalize_slug("-entry-") == "entry"

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

    @pytest.mark.parametrize(
        "value,expected",
        [("a/-/b", "a/b"), ("-/-/a", "a"), ("a/-", "a"), ("-/-", "untitled")],
    )
    def test_drop_segments_empty_after_stripping_dashes(self, value, expected):
        assert normalize_slug(value) == expected

    def test_reject_dot_dot(self):
        """Should reject paths with .."""
        with pytest.raises(UnsafePathError):
            reject_unsafe("../escape")

    def test_reject_absolute_path(self):
        """Should reject absolute paths."""
        with pytest.raises(UnsafePathError):
            reject_unsafe("/absolute/path")

    @pytest.mark.parametrize("path", [r"C:\\absolute\\path", r"\\\\server\\share\\bookmark"])
    def test_reject_windows_absolute_path(self, path):
        """Windows absolute paths must be rejected on every host platform."""
        with pytest.raises(UnsafePathError):
            reject_unsafe(path)

    def test_reject_all_dots_segment(self):
        """Should reject any segment that is entirely dots."""
        for s in ["...", "....", "a/.../b"]:
            with pytest.raises(UnsafePathError):
                reject_unsafe(s)

    def test_reject_null_byte(self):
        """Should reject NUL byte in any segment."""
        with pytest.raises(UnsafePathError):
            reject_unsafe("a/b\x00c")

    def test_accept_leading_dot_segment(self):
        """Single-dot prefix segments (e.g. .git) are accepted."""
        assert reject_unsafe(".git/config") == ".git/config"

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


class TestRejectUnsafe:
    """Test _reject_unsafe function."""

    def test_safe_path(self):
        """Should return path for safe input."""
        assert reject_unsafe("hello/world") == "hello/world"

    def test_dot_dot(self):
        """Should reject .. in a path."""
        with pytest.raises(UnsafePathError):
            reject_unsafe("hello/../world")

    def test_absolute(self):
        """Should reject absolute paths."""
        with pytest.raises(UnsafePathError):
            reject_unsafe("/absolute/path")


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

    @pytest.mark.parametrize("slug", ["/absolute/path", r"C:\\absolute\\path"])
    def test_rejects_raw_absolute_slug_before_normalizing(self, tmp_path, slug):
        """Absolute slugs must not be silently converted to relative paths."""
        with pytest.raises(UnsafePathError):
            id_to_path(tmp_path, slug)
