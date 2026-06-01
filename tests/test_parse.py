"""Parser-robustness tests against the deliberately quirky synthetic fixtures
(BRIEF §3 "Parsing robustness"): BOM, quoted embedded comma, embedded newline,
diacritics must not corrupt the row count or field values."""

from tracking import parse


def test_row_count_ignores_bom_and_embedded_newline(synthetic_send):
    # 6 data rows even though one Account_Name field contains an embedded newline
    # and the file starts with a UTF-8 BOM.
    assert parse.row_count(synthetic_send / "export_1001.csv") == 6


def test_header_strips_bom(synthetic_send):
    header = parse.read_header(synthetic_send / "export_1001.csv")
    # Without BOM tolerance this would be "﻿Subscriber Key".
    assert header[0] == "Subscriber Key"


def test_embedded_comma_and_diacritics_preserved(synthetic_send):
    df = parse.read_csv(synthetic_send / "export_1001.csv")
    assert "Smith, Jr." in df["FullName2"].tolist()      # quoted embedded comma
    assert "José" in df["First Name"].tolist()           # diacritics
    assert "Núñez" in df["Last Name"].tolist()
    assert any("\n" in v for v in df["Account Name"])    # embedded newline survived


def test_normalize_link_strips_query_and_case_and_slash():
    a = parse.normalize_link("https://x.org/Path/?utm_source=sfmc&sfmc_id=9")
    b = parse.normalize_link("https://x.org/path")
    assert a == b == "https://x.org/path"


def test_link_counts_groups_by_normalized_link(synthetic_send):
    counts = parse.link_counts(synthetic_send / "export_1003.csv")
    assert len(counts) == 6  # distinct links
    assert sum(counts.values()) == 9  # total click rows
    assert counts["https://northshore.giftplans.org/enewsletter/fall-newsletter-2026"] == 3
