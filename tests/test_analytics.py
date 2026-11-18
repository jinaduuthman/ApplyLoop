"""Tests for analytics engine."""

import pytest

from applyloop.analytics import (
    compute_funnel,
    compute_score_distribution,
    compute_time_metrics,
    compute_company_stats,
)
from applyloop.models import Application


def _make_app(num, company, role, score, status, date="2024-01-15"):
    return Application(
        num=num,
        date=date,
        company=company,
        role=role,
        score=score,
        status=status,
        pdf="x",
        report="y",
    )


class TestComputeFunnel:
    def test_empty_list(self):
        f = compute_funnel([])
        assert f.total == 0

    def test_counts_statuses(self):
        entries = [
            _make_app(1, "A", "Eng", "3/5", "Applied"),
            _make_app(2, "B", "Eng", "4/5", "Applied"),
            _make_app(3, "C", "Eng", "2/5", "Evaluated"),
            _make_app(4, "D", "Eng", "4.5/5", "Interview"),
            _make_app(5, "E", "Eng", "3/5", "Rejected"),
        ]
        f = compute_funnel(entries)
        assert f.total == 5
        assert f.applied == 2
        assert f.evaluated == 1
        assert f.interview == 1
        assert f.rejected == 1

    def test_conversion_rate(self):
        entries = [
            _make_app(1, "A", "Eng", "3/5", "Evaluated"),
            _make_app(2, "B", "Eng", "4/5", "Applied"),
        ]
        f = compute_funnel(entries)
        assert f.conversion_rate == 100.0

    def test_conversion_rate_zero_div(self):
        f = compute_funnel([])
        assert f.conversion_rate == 0.0

    def test_response_rate(self):
        entries = [
            _make_app(1, "A", "Eng", "3/5", "Applied"),
            _make_app(2, "B", "Eng", "4/5", "Responded"),
        ]
        f = compute_funnel(entries)
        assert f.response_rate == 100.0

    def test_offer_rate(self):
        entries = [
            _make_app(1, "A", "Eng", "3/5", "Applied"),
            _make_app(2, "B", "Eng", "4/5", "Applied"),
            _make_app(3, "C", "Eng", "5/5", "Offer"),
        ]
        f = compute_funnel(entries)
        assert f.offer_rate == pytest.approx(50.0)


class TestComputeScoreDistribution:
    def test_empty_list(self):
        d = compute_score_distribution([])
        assert d.total_scored == 0
        assert d.avg_score == 0.0

    def test_distribution_buckets(self):
        entries = [
            _make_app(1, "A", "E", "0.5/5", "Evaluated"),
            _make_app(2, "B", "E", "1.5/5", "Evaluated"),
            _make_app(3, "C", "E", "2.5/5", "Evaluated"),
            _make_app(4, "D", "E", "3.5/5", "Evaluated"),
            _make_app(5, "E", "E", "4.5/5", "Evaluated"),
        ]
        d = compute_score_distribution(entries)
        assert d.bucket_0_1 == 1
        assert d.bucket_1_2 == 1
        assert d.bucket_2_3 == 1
        assert d.bucket_3_4 == 1
        assert d.bucket_4_5 == 1
        assert d.total_scored == 5
        assert d.avg_score == pytest.approx(2.5)

    def test_median_even(self):
        entries = [
            _make_app(1, "A", "E", "2.0/5", "Evaluated"),
            _make_app(2, "B", "E", "4.0/5", "Evaluated"),
        ]
        d = compute_score_distribution(entries)
        assert d.median_score == pytest.approx(3.0)

    def test_skips_zero_scores(self):
        entries = [
            _make_app(1, "A", "E", "N/A", "Evaluated"),
            _make_app(2, "B", "E", "3.0/5", "Evaluated"),
        ]
        d = compute_score_distribution(entries)
        assert d.total_scored == 1


class TestComputeTimeMetrics:
    def test_empty_list(self):
        m = compute_time_metrics([])
        assert m.total_days_active == 0

    def test_single_date(self):
        entries = [_make_app(1, "A", "E", "3/5", "Applied", "2024-01-15")]
        m = compute_time_metrics(entries)
        assert m.total_days_active == 1

    def test_multiple_dates(self):
        entries = [
            _make_app(1, "A", "E", "3/5", "Applied", "2024-01-01"),
            _make_app(2, "B", "E", "3/5", "Applied", "2024-01-08"),
            _make_app(3, "C", "E", "3/5", "Applied", "2024-01-15"),
        ]
        m = compute_time_metrics(entries)
        assert m.total_days_active == 15
        assert m.applications_per_week > 0
        assert m.busiest_day != ""


class TestComputeCompanyStats:
    def test_groups_by_company(self):
        entries = [
            _make_app(1, "Acme", "Eng", "3/5", "Applied"),
            _make_app(2, "Acme", "Dev", "4/5", "Interview"),
            _make_app(3, "Beta", "Eng", "2/5", "Evaluated"),
        ]
        stats = compute_company_stats(entries)
        assert len(stats) == 2
        acme = next(s for s in stats if s["company"] == "Acme")
        assert acme["count"] == 2
        assert acme["avg_score"] == pytest.approx(3.5)
