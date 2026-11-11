"""The parsed metadata contract stays open and preserves ordered tag values."""

import pytest

from bm.io import build_text, parse_front_matter
from bm.models import metadata_tags, metadata_text


@pytest.mark.parametrize("tags", ["two,one,two", "[two, one, two]", 'json:["two","one","two"]'])
def test_parsed_metadata_preserves_unknown_fields_and_tag_order(tags):
    source = (
        f"---\nurl: https://example.com\ntags: {tags}\nadded: 2024-01-01\n"
        "custom: café\nextra: |\n  first line\n  second line\n---\nNotes\n"
    )
    meta, body = parse_front_matter(source)
    assert metadata_text(meta, "url") == "https://example.com"
    assert metadata_text(meta, "created") == "2024-01-01"
    assert metadata_tags(meta) == ["two", "one", "two"]
    assert metadata_tags(meta) is meta["tags"]
    assert meta["custom"] == "café"
    assert meta["extra"] == "first line\nsecond line"
    assert all(isinstance(value, str) for key, value in meta.items() if key != "tags")
    assert parse_front_matter(build_text(meta, body)) == (meta, body)


def test_metadata_accessors_do_not_change_or_close_the_mapping():
    meta = {"custom": "keep", "tags": ["tag"]}
    assert metadata_text(meta, "missing") == ""
    assert metadata_text(meta, "tags") == ""
    assert metadata_tags({}) == []
    assert meta == {"custom": "keep", "tags": ["tag"]}


def test_rendering_still_accepts_broader_embedding_values():
    text = build_text(
        {"url": "https://example.com", "score": 3, "custom": ["one", "two"], "empty": None},
        "notes",
    )
    meta, body = parse_front_matter(text)
    assert meta["score"] == "3"
    assert meta["custom"] == "[one, two]"
    assert "empty" not in meta
    assert body == "notes"
