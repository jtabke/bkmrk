"""Direct unit tests for internal helpers; targets mutmut gaps."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bm.commands import (
    _build_row,
    _build_search_blob,
    _entry_score,
    _export_row,
    _make_search_predicate,
    _matches_host,
    _matches_path,
    _matches_since,
    _matches_tag,
    _normalize_path_arg,
    _passes_filters,
    _resolve_filter_args,
)


class TestMatchesPath:
    def test_empty_prefix_matches_any(self):
        assert _matches_path(Path("dev/python/foo"), "") is True
        assert _matches_path(Path("dev/python/foo"), None) is True

    def test_strips_leading_and_trailing_slashes(self):
        assert _matches_path(Path("dev/python/foo"), "/dev/python/") is True
        assert _matches_path(Path("dev/python/foo"), "//dev//") is True

    def test_strip_removes_only_slashes(self):
        # Leading/trailing letters are significant path text, not strip chars.
        assert _matches_path(Path("dev"), "/XdevX/") is False

    def test_exact_match_returns_true(self):
        assert _matches_path(Path("dev/python"), "dev/python") is True

    def test_prefix_match_requires_segment_boundary(self):
        # Should not match `dev/pythonista` for prefix `dev/python` (no boundary).
        assert _matches_path(Path("dev/pythonista"), "dev/python") is False
        assert _matches_path(Path("dev/python/x"), "dev/python") is True

    def test_non_matching_returns_false(self):
        assert _matches_path(Path("news/x"), "dev") is False

    def test_blank_after_strip_treated_as_empty(self):
        assert _matches_path(Path("anything"), "/") is True


class TestMatchesTag:
    def test_no_tag_matches(self):
        assert _matches_tag(Path("a/b"), {"tags": []}, None) is True
        assert _matches_tag(Path("a/b"), {"tags": []}, "") is True

    def test_folder_segment_matches(self):
        assert _matches_tag(Path("dev/python/foo"), {"tags": []}, "python") is True

    def test_header_tag_matches(self):
        assert _matches_tag(Path("foo"), {"tags": ["news"]}, "news") is True

    def test_filename_stem_excluded_from_match(self):
        # Only parent segments count; the filename stem itself is excluded.
        assert _matches_tag(Path("python"), {"tags": []}, "python") is False

    def test_unmatched_tag_returns_false(self):
        assert _matches_tag(Path("dev/python"), {"tags": ["foo"]}, "missing") is False

    def test_meta_without_tags_key_does_not_crash(self):
        # Defensive: missing 'tags' key must not raise TypeError.
        assert _matches_tag(Path("foo"), {}, "missing") is False
        assert _matches_tag(Path("dev/foo"), {}, "dev") is True


class TestMatchesHost:
    def test_no_filter_matches(self):
        assert _matches_host({"url": "https://x.com"}, "") is True

    def test_www_prefix_ignored_on_both_sides(self):
        assert _matches_host({"url": "https://www.example.com"}, "example.com") is True
        assert _matches_host({"url": "https://example.com"}, "www.example.com") is True

    def test_case_insensitive(self):
        assert _matches_host({"url": "https://EXAMPLE.com"}, "example.com") is True

    def test_different_host_excluded(self):
        assert _matches_host({"url": "https://other.com"}, "example.com") is False

    def test_meta_without_url_key_does_not_crash(self):
        # Missing 'url' key must yield empty host, not raise.
        assert _matches_host({}, "example.com") is False


class TestMatchesSince:
    def test_no_filter_matches(self):
        assert _matches_since({"created": "2020-01-01"}, None) is True

    def test_after_threshold_matches(self):
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _matches_since({"created": "2024-06-01T00:00:00+00:00"}, cutoff) is True

    def test_before_threshold_excluded(self):
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _matches_since({"created": "2023-12-31T00:00:00+00:00"}, cutoff) is False

    def test_falls_back_to_modified_when_created_missing(self):
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _matches_since({"modified": "2024-06-01T00:00:00+00:00"}, cutoff) is True

    def test_no_dates_returns_false_under_filter(self):
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _matches_since({}, cutoff) is False

    def test_exact_boundary_inclusive(self):
        # `--since X` should include entries timestamped at exactly X.
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _matches_since({"created": "2024-01-01T00:00:00+00:00"}, cutoff) is True


class TestNormalizePathArg:
    def test_strips_slashes(self):
        assert _normalize_path_arg("/dev/python/") == "dev/python"

    def test_non_string_returns_empty(self):
        assert _normalize_path_arg(None) == ""
        assert _normalize_path_arg(123) == ""
        assert _normalize_path_arg(object()) == ""

    def test_empty_string_returns_empty(self):
        assert _normalize_path_arg("") == ""


class TestResolveFilterArgs:
    def test_string_path_honored(self):
        import argparse

        ns = argparse.Namespace(tag="dev", host="EXAMPLE.com", path="/foo/", since=None)
        tag, host, path, since = _resolve_filter_args(ns)
        assert tag == "dev"
        assert host == "example.com"
        assert path == "foo"
        assert since is None

    def test_non_string_collapses_to_neutral(self):
        import argparse

        ns = argparse.Namespace(tag=object(), host=42, path=None, since=object())
        tag, host, path, since = _resolve_filter_args(ns)
        assert tag is None
        assert host == ""
        assert path == ""
        assert since is None

    def test_empty_string_tag_is_none(self):
        import argparse

        ns = argparse.Namespace(tag="", host=None, path=None, since=None)
        tag, *_ = _resolve_filter_args(ns)
        assert tag is None


class TestPassesFilters:
    def test_all_filters_must_pass(self):
        rel = Path("dev/python/foo")
        meta = {"url": "https://example.com", "tags": ["lang"], "created": "2024-06-01"}
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _passes_filters(rel, meta, "lang", "example.com", "dev", cutoff) is True
        # Wrong tag — fails
        assert _passes_filters(rel, meta, "other", "example.com", "dev", cutoff) is False
        # Wrong host — fails
        assert _passes_filters(rel, meta, "lang", "wrong.com", "dev", cutoff) is False
        # Wrong path — fails
        assert _passes_filters(rel, meta, "lang", "example.com", "news", cutoff) is False


class TestBuildRowAndExport:
    def test_build_row_id_and_path(self):
        from bm.utils import rid

        row = _build_row(
            Path("dev/x"),
            {"url": "https://e.com", "title": "T", "tags": ["a"], "created": "c", "modified": "m"},
            None,
        )
        assert row["id"] == rid("https://e.com")
        assert row["path"] == "dev/x"
        assert row["title"] == "T"
        assert row["tags"] == ["a"]

    def test_export_row_schema(self):
        row = _export_row(
            Path("dev/x"),
            {"url": "https://e.com", "title": "T", "tags": ["a"], "created": "c", "modified": "m"},
        )
        assert set(row.keys()) == {"path", "url", "title", "tags", "created", "modified"}
        assert row["path"] == "dev/x"
        assert row["url"] == "https://e.com"


class TestEntryScore:
    def test_longer_body_wins(self):
        a = {"meta": {"title": "T"}, "body": "short", "rel": Path("a")}
        b = {"meta": {"title": "T"}, "body": "much longer body text here", "rel": Path("b")}
        # min by score → b wins because its score has more-negative body length
        assert _entry_score(b) < _entry_score(a)

    def test_when_body_tied_longer_title_wins(self):
        a = {"meta": {"title": "T"}, "body": "x", "rel": Path("a")}
        b = {"meta": {"title": "Longer"}, "body": "x", "rel": Path("b")}
        assert _entry_score(b) < _entry_score(a)

    def test_naive_created_normalized_to_utc(self):
        # Naive datetime in `created` should compare without raising and use UTC.
        e = {
            "meta": {"title": "T", "created": "2024-01-15T10:00:00"},
            "body": "x",
            "rel": Path("a"),
        }
        score = _entry_score(e)
        expected = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc).timestamp()
        assert score[2] == expected

    def test_falls_back_to_modified_when_created_missing(self):
        e = {
            "meta": {"title": "T", "modified": "2024-06-01T00:00:00+00:00"},
            "body": "x",
            "rel": Path("a"),
        }
        expected = datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp()
        assert _entry_score(e)[2] == expected

    def test_no_dates_uses_inf(self):
        e = {"meta": {"title": "T"}, "body": "x", "rel": Path("a")}
        assert _entry_score(e)[2] == float("inf")

    def test_rel_is_final_tiebreaker(self):
        a = {"meta": {"title": "T"}, "body": "x", "rel": Path("a")}
        b = {"meta": {"title": "T"}, "body": "x", "rel": Path("b")}
        # Same body, title, and date → tiebreaker is the rel string.
        assert _entry_score(a) < _entry_score(b)
        assert _entry_score(a)[3] == "a"
        assert _entry_score(b)[3] == "b"


class TestBuildSearchBlob:
    def test_default_fields(self):
        blob = _build_search_blob(
            {"title": "T", "url": "U", "tags": ["a", "b"]},
            "BODY",
            ("title", "url", "tags", "body"),
        )
        assert "T" in blob and "U" in blob and "a b" in blob and "BODY" in blob

    def test_only_tags_field(self):
        blob = _build_search_blob(
            {"title": "should not appear", "url": "should not", "tags": ["only", "tags"]},
            "body should not",
            ("tags",),
        )
        assert blob == "only tags"

    def test_unknown_field_skipped(self):
        # Defensive: an unknown field name is ignored.
        blob = _build_search_blob({"title": "T"}, "B", ("title", "garbage"))
        assert blob == "T"

    def test_missing_meta_keys_default_to_empty_strings(self):
        # No url, no tags, no title — must not raise (covers wrong defaults).
        blob = _build_search_blob({}, "BODY", ("title", "url", "tags", "body"))
        assert blob == "\n\n\nBODY"


class TestMakeSearchPredicate:
    def test_substring_and_logic(self):
        pred = _make_search_predicate("python web", use_regex=False)
        assert pred("learning python and web") is True
        assert pred("python only") is False

    def test_substring_empty_query_matches_anything(self):
        pred = _make_search_predicate("", use_regex=False)
        assert pred("") is True
        assert pred("anything") is True

    def test_regex_case_insensitive(self):
        pred = _make_search_predicate(r"^Python", use_regex=True)
        assert pred("python rocks") is True
        assert pred("learn python") is False

    def test_invalid_regex_dies(self):
        with pytest.raises(SystemExit):
            _make_search_predicate("(unbalanced", use_regex=True)
