"""Tests for the pure Netscape interchange boundary."""

from bm.netscape import build_netscape_tree, parse_netscape_html


def test_render_and_parse_preserve_escaped_bookmark_fields():
    entries = [
        (
            'folder&<"quoted">/entry',
            {
                "url": 'https://example.com/?q="quoted"&x=<tag>',
                "title": 'A <title> & "quoted"',
                "tags": ["rock&roll", "<angle>"],
                "created": "2024-01-01T00:00:00Z",
            },
        )
    ]

    html = build_netscape_tree(entries)
    parsed = parse_netscape_html(html)

    assert len(parsed) == 1
    path, meta = parsed[0]
    assert path == 'folder&<"quoted">'
    assert meta["url"] == entries[0][1]["url"]
    assert meta["title"] == entries[0][1]["title"]
    assert meta["tags"] == entries[0][1]["tags"]


def test_parse_netscape_add_date_is_utc():
    parsed = parse_netscape_html('<DT><A HREF="https://example.com" ADD_DATE="0">Example</A>')

    assert parsed[0][1]["created"] == "1970-01-01T00:00:00+00:00"
