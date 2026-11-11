"""Direct unit tests for internal helpers; targets mutmut gaps."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bm.commands import _filter_spec_from_args
from bm.errors import BmError
from bm.exporting import export_row
from bm.paths import matches_path_prefix, normalize_path_prefix
from bm.query import (
    FilterSpec,
    _build_row,
    _build_search_blob,
    _make_search_predicate,
    _matches_host,
    _matches_since,
    _matches_tag,
    passes_filters,
)


class TestMatchesPath:
    def test_empty_prefix_matches_any(self):
        assert matches_path_prefix(Path("dev/python/foo"), "") is True
        assert matches_path_prefix(Path("dev/python/foo"), None) is True

    def test_strips_leading_and_trailing_slashes(self):
        assert matches_path_prefix(Path("dev/python/foo"), "/dev/python/") is True
        assert matches_path_prefix(Path("dev/python/foo"), "//dev//") is True

    def test_strip_removes_only_slashes(self):
        # Leading/trailing letters are significant path text, not strip chars.
        assert matches_path_prefix(Path("dev"), "/XdevX/") is False

    def test_exact_match_returns_true(self):
        assert matches_path_prefix(Path("dev/python"), "dev/python") is True

    def test_prefix_match_requires_segment_boundary(self):
        # Should not match `dev/pythonista` for prefix `dev/python` (no boundary).
        assert matches_path_prefix(Path("dev/pythonista"), "dev/python") is False
        assert matches_path_prefix(Path("dev/python/x"), "dev/python") is True

    def test_non_matching_returns_false(self):
        assert matches_path_prefix(Path("news/x"), "dev") is False

    def test_blank_after_strip_treated_as_empty(self):
        assert matches_path_prefix(Path("anything"), "/") is True


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
        assert normalize_path_prefix("/dev/python/") == "dev/python"

    def test_empty_string_returns_empty(self):
        assert normalize_path_prefix("") == ""

    def test_command_adapter_rejects_non_string_filter_values(self):
        import argparse

        args = argparse.Namespace(tag=object(), host="", path="", since="")
        with pytest.raises(BmError) as exc_info:
            _filter_spec_from_args(args)
        assert exc_info.value.exit_code == 2


class TestFilterSpec:
    def test_is_immutable(self):
        from dataclasses import FrozenInstanceError

        spec = FilterSpec()
        with pytest.raises(FrozenInstanceError):
            spec.host = "example.com"

    def test_string_path_honored(self):
        import argparse

        ns = argparse.Namespace(tag="dev", host="EXAMPLE.com", path="/foo/", since=None)
        spec = _filter_spec_from_args(ns)
        assert spec == FilterSpec(tag="dev", host="example.com", path="foo", since=None)

    def test_empty_string_tag_is_none(self):
        import argparse

        ns = argparse.Namespace(tag="", host=None, path=None, since=None)
        spec = _filter_spec_from_args(ns)
        assert spec.tag is None


class TestPassesFilters:
    def test_all_filters_must_pass(self):
        rel = Path("dev/python/foo")
        meta = {"url": "https://example.com", "tags": ["lang"], "created": "2024-06-01"}
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert passes_filters(rel, meta, FilterSpec("lang", "example.com", "dev", cutoff)) is True
        # Wrong tag — fails
        assert passes_filters(rel, meta, FilterSpec("other", "example.com", "dev", cutoff)) is False
        # Wrong host — fails
        assert passes_filters(rel, meta, FilterSpec("lang", "wrong.com", "dev", cutoff)) is False
        # Wrong path — fails
        assert passes_filters(rel, meta, FilterSpec("lang", "example.com", "news", cutoff)) is False


class TestBuildRowAndExport:
    def test_build_row_id_and_path(self):
        from bm.utils import rid

        row = _build_row(
            Path("dev/x"),
            {"url": "https://e.com", "title": "T", "tags": ["a"], "created": "c", "modified": "m"},
        )
        assert row["id"] == rid("https://e.com")
        assert row["path"] == "dev/x"
        assert row["title"] == "T"
        assert row["tags"] == ["a"]

    def test_export_row_schema(self):
        row = export_row(
            Path("dev/x"),
            {"url": "https://e.com", "title": "T", "tags": ["a"], "created": "c", "modified": "m"},
        )
        assert set(row.keys()) == {"path", "url", "title", "tags", "created", "modified"}
        assert row["path"] == "dev/x"
        assert row["url"] == "https://e.com"


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

    def test_invalid_regex_raises_parser_error(self):
        import re

        with pytest.raises(re.error):
            _make_search_predicate("(unbalanced", use_regex=True)
