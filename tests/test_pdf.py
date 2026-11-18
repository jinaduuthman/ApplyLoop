"""Tests for PDF generation and ATS normalization."""

import pytest

from applyloop.pdf import normalize_text_for_ats


class TestNormalizeTextForAts:
    def test_replaces_em_dash(self):
        html = "<p>Hello\u2014World</p>"
        result, replacements = normalize_text_for_ats(html)
        assert "\u2014" not in result
        assert "Hello-World" in result
        assert replacements.get("em-dash", 0) == 1

    def test_replaces_en_dash(self):
        html = "<p>2020\u20132024</p>"
        result, replacements = normalize_text_for_ats(html)
        assert "\u2013" not in result
        assert "2020-2024" in result
        assert replacements.get("en-dash", 0) == 1

    def test_replaces_smart_quotes(self):
        html = "<p>\u201CHello\u201D and \u2018World\u2019</p>"
        result, replacements = normalize_text_for_ats(html)
        assert '"Hello"' in result
        assert "'World'" in result
        assert replacements.get("smart-double-quote", 0) == 2

    def test_replaces_ellipsis(self):
        html = "<p>wait\u2026</p>"
        result, replacements = normalize_text_for_ats(html)
        assert "wait..." in result
        assert replacements.get("ellipsis", 0) == 1

    def test_removes_zero_width_chars(self):
        html = "<p>he\u200Bllo</p>"
        result, replacements = normalize_text_for_ats(html)
        assert "\u200B" not in result
        assert "hello" in result

    def test_replaces_nbsp(self):
        html = "<p>hello\u00A0world</p>"
        result, replacements = normalize_text_for_ats(html)
        assert "\u00A0" not in result
        assert "hello world" in result

    def test_preserves_style_blocks(self):
        html = '<style>.x { content: "\u2014"; }</style><p>hello\u2014world</p>'
        result, replacements = normalize_text_for_ats(html)
        assert "\u2014" in result.split("</style>")[0]
        assert "hello-world" in result

    def test_preserves_script_blocks(self):
        html = '<script>var x = "\u2014";</script><p>test\u2014case</p>'
        result, replacements = normalize_text_for_ats(html)
        assert "\u2014" in result.split("</script>")[0]
        assert "test-case" in result

    def test_preserves_tag_attributes(self):
        html = '<a href="https://example.com/path\u2014test">link</a>'
        result, _ = normalize_text_for_ats(html)
        assert 'href="https://example.com/path\u2014test"' in result

    def test_no_replacements_clean_html(self):
        html = "<p>Hello World</p>"
        result, replacements = normalize_text_for_ats(html)
        assert result == html
        assert sum(replacements.values()) == 0

    def test_empty_html(self):
        result, replacements = normalize_text_for_ats("")
        assert result == ""
        assert len(replacements) == 0

    def test_multiple_replacements(self):
        html = "<p>\u2014\u2014\u2014</p>"
        result, replacements = normalize_text_for_ats(html)
        assert replacements.get("em-dash", 0) == 3
        assert result == "<p>---</p>"
