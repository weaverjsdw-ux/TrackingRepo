"""Content-based identification tests (BRIEF §1.1: by content, never filename).

The synthetic raw files use uninformative export_<digits>.csv names, exactly
like the real downloads, so a correct result proves filename is never used."""

import pytest

from tracking.identify import FileType, identify


@pytest.mark.parametrize("fname,expected", [
    ("export_1001.csv", FileType.TOTAL_SENT),
    ("export_1002.csv", FileType.UNIQUE_OPENS),
    ("export_1003.csv", FileType.UNIQUE_CLICKS),    # many distinct links
    ("export_1004.csv", FileType.BOOKLET_CLICKS),   # one distinct link
    ("export_1005.csv", FileType.BOUNCE),
    ("export_1006.csv", FileType.UNSUBSCRIBES),
    ("sd_Northshore College - Lead Scoring20260901.csv", FileType.LEAD_SCORING),
])
def test_identify_by_content(synthetic_send, fname, expected):
    assert identify(synthetic_send / fname).type is expected


def test_click_split_by_distinct_link_count(synthetic_send):
    master = identify(synthetic_send / "export_1003.csv")
    booklet = identify(synthetic_send / "export_1004.csv")
    assert master.type is FileType.UNIQUE_CLICKS and master.distinct_links == 6
    assert booklet.type is FileType.BOOKLET_CLICKS and booklet.distinct_links == 1


def test_unrecognized_csv_fails_loud(tmp_path):
    bogus = tmp_path / "export_9999.csv"
    bogus.write_text("Col A,Col B\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unrecognized CSV"):
        identify(bogus)
