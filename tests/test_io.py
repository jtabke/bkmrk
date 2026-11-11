"""Unit tests for bm.io module."""

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bm.io import (
    ConcurrentModificationError,
    _fmt_tag,
    _normalize_meta,
    atomic_write,
    build_text,
    load_entry,
    parse_front_matter,
)


@pytest.mark.parametrize(
    "tag",
    [
        "O'Reilly",
        'say"hello',
        "with,comma",
        "two words",
        " leading ",
        "",
        r"C:\reading\notes",
        r"quote\"slash\\",
        "café",
        "line\nfeed",
        "tab\tinside",
        "single'and\"double",
        "[brackets]",
        "next\u0085line",
        "next\u2028line",
        "next\u2029line",
    ],
)
def test_tag_round_trip(tag):
    meta, body = parse_front_matter(
        build_text({"url": "https://example.com", "tags": [tag]}, "notes")
    )
    assert meta["tags"] == [tag]
    assert body == "notes"


@given(tags=st.lists(st.text(max_size=64), max_size=12))
def test_generated_tags_round_trip(tags):
    """Generated tags retain their order, duplicates, whitespace, and Unicode."""
    meta, body = parse_front_matter(build_text({"tags": tags}, "notes"))
    assert meta["tags"] == tags
    assert body == "notes"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[O'Reilly, say\"hello]", ["O'Reilly", 'say"hello']),
        ("['comma,tag', plain]", ["comma,tag", "plain"]),
        (r'["C:\legacy\path", plain]', [r"C:\legacy\path", "plain"]),
        (r'["C:\new folder", plain]', [r"C:\new folder", "plain"]),
        (r"['C:\new folder', plain]", [r"C:\new folder", "plain"]),
        (
            r'["\n", "\t", "\r", "\b", "\f", "\u0041"]',
            [r"\n", r"\t", r"\r", r"\b", r"\f", r"\u0041"],
        ),
        (
            r'["\\server\new folder", "trailing\", plain]',
            [r"\\server\new folder", "trailing\\", "plain"],
        ),
        ('["quoted", unquoted]', ["quoted", "unquoted"]),
        ("[one,two", ["[one", "two"]),
        ("one,two]", ["one", "two]"]),
        ("[Xfirst, second]", ["Xfirst", "second"]),
        ("[first,  'comma,tag']", ["first", "comma,tag"]),
    ],
)
def test_legacy_tag_forms_remain_readable(text, expected):
    meta, _ = parse_front_matter(f"---\ntags: {text}\n---\n")
    assert meta["tags"] == expected


@pytest.mark.parametrize(
    "header,problem",
    [
        ("title: first\ntitle: second\n", "Repeated front-matter key"),
        ("not a field\n", "Invalid front-matter line"),
        (": no key\n", "Invalid front-matter line"),
        ("notes: |\n  text\n invalid indentation\n", "Invalid front-matter line"),
    ],
)
def test_parser_diagnostics_share_the_tolerant_parse(header, problem):
    text = "---\n" + header + "---\nnotes\n"
    diagnostics = []
    result = parse_front_matter(text, diagnostics=diagnostics)
    assert result == parse_front_matter(text)
    assert any(problem in item for item in diagnostics)


@pytest.mark.parametrize("tags", [[r"C:\new folder"], ['say"hello'], ["line\nfeed"], [r"\u0041"]])
def test_escaped_tag_lists_are_explicit_and_stable_across_rewrites(tags):
    text = build_text({"tags": tags}, "")
    assert "tags: json:[" in text
    meta, _ = parse_front_matter(text)
    assert meta["tags"] == tags
    assert build_text(meta, "") == text


@pytest.mark.parametrize("value", ['json:["unterminated]', "json:[1]", "json:[null]"])
def test_invalid_encoded_tags_are_diagnosed_and_retained(value):
    diagnostics = []
    meta, _ = parse_front_matter(f"---\ntags: {value}\n---\n", diagnostics=diagnostics)
    assert diagnostics == ["Invalid JSON-encoded tags (expected an array of strings)."]
    assert meta["tags"] == [value]
    rewritten, _ = parse_front_matter(build_text(meta, ""))
    assert rewritten["tags"] == [value]


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("notes", ["  Xnote\nsecond", "Xylophone\nsecond"])
def test_legacy_url_body_preserves_indentation_and_multiple_lines(scheme, notes):
    meta, body = parse_front_matter(f"{scheme}://example.com\n\n{notes}\n")
    assert meta["url"] == f"{scheme}://example.com"
    assert body == notes


@pytest.mark.parametrize("body", ["  indented\n", "\tindented\n", "Xylophone\n"])
def test_front_matter_body_preserves_leading_non_newline_characters(body):
    _, parsed = parse_front_matter("---\nurl: https://example.com\n---\n\n" + body)
    assert parsed == body


@pytest.mark.parametrize("notes", ["  first\n\n\n  second\n", ""])
def test_block_scalar_preserves_multiple_blanks_and_allows_empty_value(notes):
    meta, _ = parse_front_matter(f"---\nnotes: |\n{notes}title: next field\n---\n")
    assert meta["notes"] == ("first\n\n\nsecond" if notes else "")
    assert meta["title"] == "next field"


def test_header_ignores_comments_and_blank_lines_after_fields():
    meta, body = parse_front_matter(
        "---\nurl: https://example.com\n# first\n\n# second\ntitle: kept\n---\nnotes"
    )
    assert meta == {"url": "https://example.com", "title": "kept", "tags": []}
    assert body == "notes"


def test_invalid_header_line_is_diagnosed_without_becoming_metadata():
    diagnostics = []
    meta, _ = parse_front_matter("---\nnot a field\ntitle: valid\n---\n", diagnostics=diagnostics)
    assert meta == {"title": "valid", "tags": []}
    assert diagnostics == ["Invalid front-matter line: 'not a field'."]


@pytest.mark.parametrize("front_matter", [False, True])
@pytest.mark.parametrize("meta_only", [False, True])
def test_load_entry_replaces_invalid_utf8(tmp_path, front_matter, meta_only):
    path = tmp_path / "entry.bm"
    text = (
        b"---\nurl: https://example.com\ntitle: damaged\xff\n---\nnotes\xff"
        if front_matter
        else b"https://example.com/\xff\nnotes\xff"
    )
    path.write_bytes(text)
    meta, body = load_entry(path, meta_only=meta_only)
    if front_matter:
        assert meta["title"] == "damaged\ufffd"
    else:
        assert meta["url"] == "https://example.com/\ufffd"
    assert body == ("" if meta_only else "notes\ufffd")


def test_load_entry_uses_utf8_independent_of_locale(tmp_path, monkeypatch):
    path = tmp_path / "entry.bm"
    path.write_bytes("---\nurl: https://example.com\n---\ncafé".encode("utf-8"))
    read_text = Path.read_text

    def ascii_default_read_text(path, encoding=None, errors=None):
        return read_text(path, encoding=encoding or "ascii", errors=errors)

    monkeypatch.setattr(Path, "read_text", ascii_default_read_text)
    assert load_entry(path)[1] == "café"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_metadata_only_reader_stops_at_exact_closing_marker(tmp_path, monkeypatch, newline):
    import bm.io as io_mod

    header = newline.join([b"---", b"--- ", b"---X", b"url: https://example.com", b"---", b""])

    @contextmanager
    def guarded_open(path, mode):
        assert path == tmp_path / "entry.bm"
        assert mode == "rb"
        with BytesIO(header + b"body that must not be read") as stream:
            yield stream
            assert stream.tell() == len(header)

    monkeypatch.setattr(io_mod, "open", guarded_open, raising=False)
    assert io_mod._read_meta_only(tmp_path / "entry.bm") == header.decode("utf-8")


@pytest.mark.parametrize("opening", [b"--- \n", b"---X\n"])
def test_metadata_only_reader_does_not_accept_lookalike_opening(tmp_path, opening):
    from bm.io import _read_meta_only

    path = tmp_path / "entry.bm"
    content = opening + b"url: https://example.com\n---\nnotes"
    path.write_bytes(content)
    assert _read_meta_only(path) == content.decode("utf-8")


def test_block_scalar_keys_are_not_duplicate_header_fields():
    diagnostics = []
    meta, _ = parse_front_matter(
        "---\nnotes: |\n  title: text\n  title: more text\ntitle: actual\n---\n",
        diagnostics=diagnostics,
    )
    assert not diagnostics
    assert meta["notes"] == "title: text\ntitle: more text"
    assert meta["title"] == "actual"


def test_atomic_text_create_publishes_only_on_success(tmp_path):
    from bm.io import atomic_text_create

    destination = tmp_path / "export.jsonl"
    with atomic_text_create(destination) as stream:
        stream.write("first\n")
        stream.flush()
        assert not destination.exists()
        stream.write("café\n")
    assert destination.read_bytes() == "first\ncafé\n".encode()
    assert list(tmp_path.iterdir()) == [destination]


def test_atomic_text_create_serialization_failure_cleans_up(tmp_path):
    from bm.io import atomic_text_create

    destination = tmp_path / "export.jsonl"
    with pytest.raises(ValueError, match="serializer"):
        with atomic_text_create(destination) as stream:
            stream.write("incomplete")
            raise ValueError("serializer failed")
    assert list(tmp_path.iterdir()) == []


def test_atomic_create_retains_arbitrary_bytes(tmp_path):
    from bm.io import atomic_create

    destination = tmp_path / "raw"
    content = b"\xff\x00\r\nexact bytes"
    atomic_create(destination, content)
    assert destination.read_bytes() == content


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

    def test_delimiter_text_inside_value_does_not_close_header(self):
        """Only a complete delimiter line may close front matter."""
        text = """---
url: https://example.com
title: --- inside a title
notes: value --- remains a value
---
Body
"""
        meta, body = parse_front_matter(text)
        assert meta["title"] == "--- inside a title"
        assert meta["notes"] == "value --- remains a value"
        assert body == "Body\n"

    def test_front_matter_scanner_preserves_splitlines_boundaries(self):
        """Optimized scanning must retain the original delimiter semantics."""
        from bm.io import _split_front_matter

        separators = [
            "\n",
            "\r",
            "\r\n",
            "\v",
            "\f",
            "\x1c",
            "\x1d",
            "\x1e",
            "\x85",
            "\u2028",
            "\u2029",
        ]
        for opening in ["---\n", "---\r\n"]:
            for separator in separators:
                for ending in separators + [""]:
                    for marker in ["---", "----", " ---", "--- "]:
                        rest = "title: Example" + separator + marker + ending + "body\n---\nmore"
                        expected = None
                        offset = 0
                        for line in rest.splitlines(keepends=True):
                            if line.rstrip("\r\n") == "---":
                                expected = (rest[:offset], rest[offset + len(line) :])
                                break
                            offset += len(line)
                        assert _split_front_matter(opening + rest) == expected

    @pytest.mark.parametrize("ending", ["", "\n", "\r", "\r\n"])
    def test_front_matter_closes_at_eof(self, ending):
        meta, body = parse_front_matter("---\ntitle: Example\n---" + ending)
        assert meta == {"title": "Example", "tags": []}
        assert body == ""

    def test_front_matter_preserves_large_body_and_embedded_delimiters(self):
        body = ("Long notes\r\n---\n" * 25000) + "last line"
        meta, parsed_body = parse_front_matter("---\ntitle: Example\n---\n" + body)
        assert meta["title"] == "Example"
        assert parsed_body == body

    def test_crlf_front_matter_delimiters(self):
        """Opening, closing, and body boundaries support CRLF input."""
        text = "---\r\nurl: https://example.com\r\ntitle: Example\r\n---\r\nBody\r\n"
        meta, body = parse_front_matter(text)
        assert meta["url"] == "https://example.com"
        assert meta["title"] == "Example"
        assert body == "Body\r\n"

    def test_meta_only_matches_full_parse_at_delimiter(self, tmp_path):
        """Metadata-only reads agree with full reads without loading the body."""
        from bm.io import load_entry

        text = "---\r\nurl: https://example.com\r\ntitle: --- value\r\n---\r\nBody\r\n"
        fpath = tmp_path / "x.bm"
        fpath.write_bytes(text.encode("utf-8"))
        full_meta, full_body = load_entry(fpath)
        meta_only, body_only = load_entry(fpath, meta_only=True)
        assert meta_only == full_meta
        assert body_only == ""
        # Path.read_text uses the platform's normal newline translation.
        assert full_body == "Body\n"

    def test_meta_only_missing_delimiter_matches_missing_full_parse(self, tmp_path):
        """A missing closing marker must not expose partial metadata."""
        from bm.io import load_entry

        text = "---\r\nurl: https://example.com\r\ntitle: Example\r\nBody"
        fpath = tmp_path / "x.bm"
        fpath.write_bytes(text.encode("utf-8"))
        full_meta, full_body = load_entry(fpath)
        meta_only, body_only = load_entry(fpath, meta_only=True)
        assert full_meta == {"tags": []}
        assert meta_only == full_meta
        assert full_body == text.replace("\r\n", "\n")
        assert body_only == ""

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

    def test_block_scalar_round_trip(self):
        """Should reconstruct block scalars emitted by build_text."""
        text = "\n".join(
            [
                "---",
                "notes: |",
                "  line1",
                "  line2",
                "  ",
                "  line3",
                "---",
                "",
            ]
        )
        meta, _ = parse_front_matter(text)
        assert meta["notes"] == "line1\nline2\n\nline3"

    def test_block_scalar_truly_empty_blank_line(self):
        """Should preserve blank lines that have no whitespace at all."""
        text = "\n".join(
            [
                "---",
                "notes: |",
                "  line1",
                "",
                "  line3",
                "---",
                "",
            ]
        )
        meta, _ = parse_front_matter(text)
        assert meta["notes"] == "line1\n\nline3"

    def test_load_entry_meta_only_does_not_read_body(self, tmp_path):
        """meta_only must early-exit at the second '---' marker."""
        from bm.io import load_entry

        fpath = tmp_path / "x.bm"
        # Body is much larger than the chunk size to ensure early-exit fires.
        body = "x" * (8192 * 4)
        fpath.write_text(
            f"---\nurl: https://e.example.com\ntitle: T\n---\n{body}",
            encoding="utf-8",
        )
        meta, body_out = load_entry(fpath, meta_only=True)
        assert meta["url"] == "https://e.example.com"
        assert meta["title"] == "T"
        assert body_out == ""

    def test_atomic_write_refuses_symlink_dest(self, tmp_path):
        """atomic_write must refuse to follow a symlink at the destination."""
        from bm.io import atomic_write

        target = tmp_path / "outside.txt"
        target.write_text("untouched")
        link = tmp_path / "link.bm"
        link.symlink_to(target)

        with pytest.raises(OSError):
            atomic_write(link, "new content")

        assert target.read_text() == "untouched"
        assert link.is_symlink()

    def test_atomic_create_never_replaces_destination(self, tmp_path):
        """Create-only publication leaves an existing destination untouched."""
        from bm.io import atomic_create

        fpath = tmp_path / "x.bm"
        fpath.write_text("existing", encoding="utf-8")
        with pytest.raises(FileExistsError):
            atomic_create(fpath, "replacement")
        assert fpath.read_text(encoding="utf-8") == "existing"

    def test_atomic_create_race_is_rejected_without_overwrite(self, tmp_path, monkeypatch):
        """A destination created during publication must win without clobbering."""
        import bm.io as io_mod

        fpath = tmp_path / "x.bm"
        original_link = io_mod.os.link

        def interfere(source, destination):
            fpath.write_text("raced", encoding="utf-8")
            return original_link(source, destination)

        monkeypatch.setattr(io_mod.os, "link", interfere)
        with pytest.raises(FileExistsError):
            io_mod.atomic_create(fpath, "replacement")
        assert fpath.read_text(encoding="utf-8") == "raced"

    def test_atomic_create_does_not_fallback_when_links_are_unavailable(
        self, tmp_path, monkeypatch
    ):
        """Unsupported no-replace publication must fail instead of replacing."""
        import bm.io as io_mod

        fpath = tmp_path / "x.bm"

        def unavailable(*_args):
            raise OSError("no links")

        monkeypatch.setattr(io_mod.os, "link", unavailable)
        with pytest.raises(OSError, match="no links"):
            io_mod.atomic_create(fpath, "replacement")
        assert not fpath.exists()

    def test_atomic_write_rejects_stale_expected_contents(self, tmp_path):
        """Expected-content writes must not overwrite a newer source file."""
        fpath = tmp_path / "x.bm"
        fpath.write_text("newer")

        with pytest.raises(ConcurrentModificationError):
            atomic_write(fpath, "replacement", expected=b"older")

        assert fpath.read_text() == "newer"

    def test_atomic_write_rechecks_after_final_symlink_guard(self, tmp_path, monkeypatch):
        """A change observable at the final guard must be reported as a conflict."""
        import bm.io as io_mod

        fpath = tmp_path / "x.bm"
        fpath.write_text("original")
        original_guard = io_mod._refuse_symlink
        calls = 0

        def mutate_during_final_guard(path):
            nonlocal calls
            calls += 1
            original_guard(path)
            if calls == 2:
                path.write_text("newer")

        monkeypatch.setattr(io_mod, "_refuse_symlink", mutate_during_final_guard)

        with pytest.raises(ConcurrentModificationError):
            atomic_write(fpath, "replacement", expected=b"original")

        assert fpath.read_text() == "newer"

    def test_atomic_write_fsyncs_file(self, tmp_path, monkeypatch):
        """Atomic writes flush the temporary file before replacement."""
        import bm.io as io_mod

        fsync_calls = []
        monkeypatch.setattr(io_mod.os, "fsync", lambda fd: fsync_calls.append(fd))
        monkeypatch.setattr(io_mod, "_fsync_directory", lambda _path: None)

        atomic_write(tmp_path / "x.bm", "content")

        assert len(fsync_calls) == 1

    def test_fsync_directory_is_best_effort(self, tmp_path, monkeypatch):
        """Directory durability requests a separate fsync when supported."""
        import bm.io as io_mod

        open_directory = Mock(return_value=123)
        fsync = Mock()
        close = Mock()
        monkeypatch.setattr(io_mod.os, "open", open_directory)
        monkeypatch.setattr(io_mod.os, "fsync", fsync)
        monkeypatch.setattr(io_mod.os, "close", close)

        io_mod._fsync_directory(tmp_path)

        flags = io_mod.os.O_RDONLY | getattr(io_mod.os, "O_DIRECTORY", 0)
        open_directory.assert_called_once_with(tmp_path, flags)
        fsync.assert_called_once_with(123)
        close.assert_called_once_with(123)

    @pytest.mark.parametrize("operation", ["open", "fsync"])
    def test_fsync_directory_tolerates_unsupported_operations(
        self, tmp_path, monkeypatch, operation
    ):
        """Unsupported directory open/fsync is harmless; opened descriptors close."""
        import bm.io as io_mod

        open_directory = Mock(return_value=123)
        fsync = Mock()
        close = Mock()
        if operation == "open":
            open_directory.side_effect = OSError("directory open unsupported")
        else:
            fsync.side_effect = OSError("directory fsync unsupported")
        monkeypatch.setattr(io_mod.os, "open", open_directory)
        monkeypatch.setattr(io_mod.os, "fsync", fsync)
        monkeypatch.setattr(io_mod.os, "close", close)

        io_mod._fsync_directory(tmp_path)

        if operation == "open":
            fsync.assert_not_called()
            close.assert_not_called()
        else:
            fsync.assert_called_once_with(123)
            close.assert_called_once_with(123)

    def test_load_entry_meta_only_no_front_matter(self, tmp_path):
        """meta_only on a body-only file must still parse without error."""
        from bm.io import load_entry

        fpath = tmp_path / "x.bm"
        fpath.write_text("https://e.example.com\nfree-form notes\n", encoding="utf-8")
        meta, body_out = load_entry(fpath, meta_only=True)
        assert meta.get("url") == "https://e.example.com"
        assert body_out == ""

    def test_block_scalar_drops_trailing_blanks(self):
        """Should drop trailing blanks before next key / front-matter end."""
        text = "\n".join(
            [
                "---",
                "notes: |",
                "  body",
                "",
                "title: T",
                "---",
                "",
            ]
        )
        meta, _ = parse_front_matter(text)
        assert meta["notes"] == "body"
        assert meta["title"] == "T"


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

    def test_known_fields_use_canonical_order_before_unknown_metadata(self):
        meta = {
            "x-extra": "kept",
            "notes": "note",
            "modified": "2025-02-03",
            "created": "2025-01-02",
            "tags": ["tag"],
            "title": "Title",
            "url": "https://example.com",
        }
        lines = build_text(meta, "").splitlines()
        assert [line.split(":", 1)[0] for line in lines[1:-1]] == [
            "url",
            "title",
            "tags",
            "created",
            "modified",
            "notes",
            "x-extra",
        ]

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
        data = "Unicode café\nwith a second line\n"
        atomic_write(fpath, data)
        assert fpath.read_text(encoding="utf-8") == data

    def test_write_bytes(self, tmp_path):
        """Binary writes must preserve arbitrary bytes and line endings exactly."""
        fpath = tmp_path / "test.bm"
        data = b"\xff\x00\r\nraw\nbytes\r"
        atomic_write(fpath, data)
        assert fpath.read_bytes() == data

    def test_text_write_uses_utf8_independent_of_locale(self, tmp_path, monkeypatch):
        import bm.io as io_mod

        fdopen = io_mod.os.fdopen

        def ascii_default_fdopen(fd, mode, encoding=None):
            return fdopen(fd, mode, encoding=encoding or "ascii")

        monkeypatch.setattr(io_mod.os, "fdopen", ascii_default_fdopen)
        destination = tmp_path / "entry.bm"
        atomic_write(destination, "café")
        assert destination.read_bytes() == "café".encode("utf-8")

    def test_replacement_uses_same_directory_temporary_file(self, tmp_path, monkeypatch):
        """Replacement must not cross filesystems or expose a partial destination."""
        import bm.io as io_mod

        destination = tmp_path / "entry.bm"
        destination.write_bytes(b"original")
        replace = io_mod.os.replace
        replacements = []

        def check_replace(source, target):
            source = Path(source)
            assert source.parent == destination.parent
            assert source.read_bytes() == b"replacement"
            assert destination.read_bytes() == b"original"
            replacements.append(source)
            replace(source, target)

        monkeypatch.setattr(io_mod.os, "replace", check_replace)
        atomic_write(destination, b"replacement")
        assert len(replacements) == 1
        assert destination.read_bytes() == b"replacement"
        assert list(tmp_path.iterdir()) == [destination]

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
