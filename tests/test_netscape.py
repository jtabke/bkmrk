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


def test_parse_netscape_compact_nested_folders():
    text = (
        "<DL><p><DT><H3>Development</H3><DL><p>"
        "<DT><H3>Python</H3><DL><p>"
        "<DT><A HREF=https://example.com ADD_DATE=0 TAGS=code,docs>Example</A>"
        "</DL><p></DL><p></DL><p>"
    )

    parsed = parse_netscape_html(text)

    assert parsed == [
        (
            "Development/Python",
            {
                "url": "https://example.com",
                "title": "Example",
                "tags": ["code", "docs"],
                "created": "1970-01-01T00:00:00+00:00",
            },
        )
    ]


def test_parse_netscape_accepts_mixed_case_single_quoted_attributes():
    text = (
        "<dL><p><dT><h3 class='folder'>Tools</H3><DL><p>"
        "<dT><a hReF='https://example.com/?a=1&amp;b=2' "
        "tAgS='one,two' aDd_DaTe='0'>A <b>tool</b></a></dL><p></dL><p>"
    )

    parsed = parse_netscape_html(text)

    assert parsed == [
        (
            "Tools",
            {
                "url": "https://example.com/?a=1&b=2",
                "title": "A tool",
                "tags": ["one", "two"],
                "created": "1970-01-01T00:00:00+00:00",
            },
        )
    ]


def test_parse_netscape_decodes_entities_once():
    text = (
        "<DT><H3>F &amp; &amp;amp; &#34;quoted&#34;</H3>"
        '<DL><p><DT><A HREF="https://example.com/?q=1&amp;x=2" '
        'TAGS="one&amp;two,literal&amp;#34;">'
        "Title &amp; &amp;amp; &#34;quoted&#34;</A></DL><p>"
    )

    parsed = parse_netscape_html(text)

    assert parsed == [
        (
            'F & &amp; "quoted"',
            {
                "url": "https://example.com/?q=1&x=2",
                "title": 'Title & &amp; "quoted"',
                "tags": ["one&two", "literal&#34;"],
            },
        )
    ]


def test_parse_netscape_keeps_truncated_trailing_bookmark():
    parsed = parse_netscape_html('<A HREF="https://example.com">Truncated title')

    assert parsed[0][1]["url"] == "https://example.com"
    assert parsed[0][1]["title"] == "Truncated title"


def test_parse_netscape_finishes_bookmark_before_next_anchor():
    parsed = parse_netscape_html(
        '<A HREF="https://one.example">One<A HREF="https://two.example">Two</A>'
    )

    assert [meta["url"] for _, meta in parsed] == [
        "https://one.example",
        "https://two.example",
    ]


def test_parse_netscape_uses_default_created_without_add_date():
    parsed = parse_netscape_html(
        '<DT><A HREF="https://example.com">Example</A>',
        default_created="2024-01-01T00:00:00+00:00",
    )

    assert parsed[0][1]["created"] == "2024-01-01T00:00:00+00:00"
