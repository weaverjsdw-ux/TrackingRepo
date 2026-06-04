"""BH booklet-aggregation tests (BRIEF §2.A §11a) for the operator's two paths.

The real-data finished/raw values (both 21) are asserted in test_real_golden.py;
here we use synthetic and minimal hand-built link maps."""

import pytest

from tracking import bh
from tracking.parse import link_counts


# --- FALLBACK path: clicks-derive from the master ---

def test_clicks_derive_common_parent_excludes_cta(synthetic_send):
    # Survivors after excluding article/system/CTA(/requestguide) -> landing page.
    result = bh.derive_from_master(link_counts(synthetic_send / "export_1003.csv"))
    assert result.bh == 3
    assert result.method == "clicks-derive(common-parent)"
    assert result.booklet_links == [
        "https://northshore.giftplans.org/enewsletter/fall-newsletter-2026"
    ]


def test_clicks_derive_excludes_requestguide_cta():
    # Mirrors the clean raw Bradley send: landing page survives, /requestguide
    # (CTA) and unsub (system) are removed.
    counts = {
        "https://bradley.giftplans.org/enewsletter/giving-thought-spring-2026/article-1": 19,
        "https://bradley.giftplans.org/enewsletter/giving-thought-spring-2026": 21,
        "http://click.email.giftplans.org/unsub_center.aspx": 12,
        "https://bradley.giftplans.org/requestguide": 4,
    }
    result = bh.derive_from_master(counts)
    assert result.bh == 21
    assert result.method == "clicks-derive(common-parent)"


def test_clicks_derive_zero_candidates_fails_loud():
    counts = {
        "https://x.org/enewsletter/n/article-1": 5,
        "http://click.email.giftplans.org/unsub_center.aspx": 3,
    }
    with pytest.raises(bh.BHError, match="ZERO"):
        bh.derive_from_master(counts)


def test_clicks_derive_ambiguous_fails_loud():
    counts = {"https://x.org/promo-a": 5, "https://x.org/promo-b": 4}
    with pytest.raises(bh.BHError, match="AMBIGUOUS"):
        bh.derive_from_master(counts)


# --- PRIMARY path + reconciliation via resolve_bh ---

def test_resolve_prefers_request_file(synthetic_send):
    counts = link_counts(synthetic_send / "export_1003.csv")
    result = bh.resolve_bh(request_count=3, request_link="https://x/landing",
                           master_link_counts=counts)
    assert result.bh == 3
    assert result.method == "request-file"
    assert result.warning is None  # request (3) agrees with derive (3)


def test_resolve_trusts_request_export_even_when_master_differs(synthetic_send):
    # Operator decision: the request export is authoritative; a differing master
    # derive does NOT override it or raise a warning (they differ by design).
    counts = link_counts(synthetic_send / "export_1003.csv")  # would derive to 3
    result = bh.resolve_bh(request_count=6, request_link="https://x/requestguide",
                           master_link_counts=counts)
    assert result.bh == 6
    assert result.method == "request-file"
    assert result.warning is None


def test_resolve_falls_back_to_derive_when_no_request_file(synthetic_send):
    counts = link_counts(synthetic_send / "export_1003.csv")
    result = bh.resolve_bh(request_count=None, master_link_counts=counts)
    assert result.bh == 3
    assert result.method == "clicks-derive(common-parent)"


def test_resolve_no_sources_fails_loud():
    with pytest.raises(bh.BHError, match="Cannot compute BH"):
        bh.resolve_bh(request_count=None, master_link_counts=None)
