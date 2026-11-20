"""URL structure inspection and explicit per-operation policies."""

import json
from unittest.mock import patch

import pytest

from bm.backup import apply_restore, plan_restore
from bm.cli import main
from bm.commands import _open_url
from bm.doctor import inspect_store
from bm.errors import BmError
from bm.importing import import_netscape
from bm.io import build_text
from bm.store import Store
from bm.urls import ALLOWED_URL_SCHEMES, inspect_url


@pytest.mark.parametrize(
    "url,supported,problem",
    [
        ("https://example.com/path", True, None),
        ("https://example.com:8443/path", True, None),
        ("HTTP://example.com", True, None),
        ("mailto:reader@example.com", True, None),
        ("ftp://example.com/file", True, None),
        ("https:///missing-host", True, "missing URL host"),
        ("mailto:", True, "missing mail address"),
        ("https://example.com:bad", True, "Port could not be cast"),
        ("https://example.com:99999", True, "Port out of range"),
        ("javascript:alert(1)", False, None),
        ("example.com/path", False, None),
        ("https://[broken", False, "Invalid IPv6 URL"),
    ],
)
def test_inspect_url(url, supported, problem):
    inspection = inspect_url(url)
    assert inspection.supported is supported
    if problem is None:
        assert inspection.problem is None
    else:
        assert problem in inspection.problem


@pytest.mark.parametrize(
    "args",
    [
        ["list", "--json"],
        ["search", "example", "--jsonl"],
        ["export", "json"],
        ["export", "json", "--jsonl"],
        ["export", "netscape"],
    ],
)
def test_bad_urls_do_not_abort_host_filtered_scans(store, write_bm, capsys, args):
    write_bm("keep", url="https://example.com/good")
    write_bm("broken", url="https://[broken")
    write_bm("bad-port", url="https://example.com:bad")
    assert main(["--store", str(store), *args, "--host", "example.com"]) == 0
    output = capsys.readouterr()
    assert "https://example.com/good" in output.out
    assert "bad-port" not in output.out and "broken" not in output.out
    assert "skipping bad-port" in output.err and "skipping broken" in output.err


def test_dedupe_continues_past_bad_urls_and_keeps_their_bytes(store, write_bm, capsys):
    damaged = {
        write_bm(name, url=url)
        for name, url in [
            ("broken", "https://[broken"),
            ("bad-a", "https://example.com:bad"),
            ("bad-b", "https://example.com:bad"),
        ]
    }
    originals = {path: path.read_bytes() for path in damaged}
    for name in ("one", "two"):
        write_bm(name, url="https://good.example")
    for flags in (["--dry-run"], []):
        assert main(["--store", str(store), "dedupe", "--json", *flags]) == 0
        output = capsys.readouterr()
        assert [a["canonical_url"] for a in json.loads(output.out)] == ["good.example"]
        assert all(f"skipping {path.stem}" in output.err for path in damaged)
    assert len(list(store.glob("*.bm"))) == 4
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("allow_scheme", [False, True])
def test_unparseable_url_never_reaches_browser_even_with_scheme_override(allow_scheme):
    with patch("webbrowser.open") as browser:
        with pytest.raises(BmError, match="invalid URL") as error:
            _open_url("https://[broken", allow_scheme=allow_scheme)
    assert error.value.exit_code == 2
    browser.assert_not_called()


@pytest.mark.parametrize("url", ["https:///missing-host", "https://example.com:bad", "mailto:"])
def test_restore_preserves_structure_browser_import_rejects_and_doctor_diagnoses(tmp_path, url):
    content = build_text({"url": url}, "preserved notes\n")
    store = Store(tmp_path / "restored")
    plan = plan_restore(
        store, [{"path": "entry.bm", "content": content}], ALLOWED_URL_SCHEMES, force=False
    )
    assert not store.root.exists()
    apply_restore(store, plan)
    assert (store.root / "entry.bm").read_text(encoding="utf-8") == content
    assert any(
        item.code == "invalid_url" for item in inspect_store(store, ALLOWED_URL_SCHEMES).findings
    )

    browser_store = Store(tmp_path / "browser")
    html = f'<DL><DT><A HREF="{url}">Page</A></DL>'
    counts = import_netscape(browser_store, html, dry_run=True)
    assert counts.invalid_url == 1
    assert counts.added == 0
    assert not browser_store.root.exists()
