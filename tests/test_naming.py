"""Golden tests for the source-of-truth naming function (BRIEF §1.1, §3).

The expected strings are taken directly from the real FINISHED filenames in the
example folder -- these are send-identity-independent structural truths, so they
are committed here without any PII."""

import pytest

from tracking import naming


def test_parse_send_identity_bradley():
    ident = naming.parse_send_identity("Bradley University - Spring 2026 eNL")
    assert ident.client == "Bradley University"
    assert ident.season == "Spring"
    assert ident.year == "2026"
    assert ident.type == "eNL"
    assert ident.prefix == "Bradley University Spring 2026 eNL"


def test_finished_csv_names_match_golden_convention():
    ident = naming.parse_send_identity("Bradley University - Spring 2026 eNL")
    # Exact finished names observed in the example folder.
    assert naming.finished_csv_name(ident, "Unique Opens") == \
        "Bradley University Spring 2026 eNL - Unique Opens.csv"
    assert naming.finished_csv_name(ident, "Hard Bounces") == \
        "Bradley University Spring 2026 eNL - Hard Bounces.csv"
    assert naming.finished_csv_name(ident, "Total Sent") == \
        "Bradley University Spring 2026 eNL - Total Sent.csv"


def test_finished_pdf_name():
    ident = naming.parse_send_identity("Bradley University - Spring 2026 eNL")
    assert naming.finished_pdf_name(ident) == \
        "Bradley University Spring 2026 eNL - Engagement Tracking Report.pdf"


def test_email_subject_uses_em_dash():
    ident = naming.parse_send_identity("Bradley University - Spring 2026 eNL")
    assert naming.email_subject(ident) == \
        "Engagement Tracking — Bradley University Spring 2026 eNL"


def test_request_file_name_uses_fixed_label():
    # Operator decision: the request (booklet) file renames to "... - Request Your.csv".
    ident = naming.parse_send_identity("Bradley University - Spring 2026 eNL")
    assert naming.finished_csv_name(ident, naming.REQUEST_FILE_DESCRIPTION) == \
        "Bradley University Spring 2026 eNL - Request Your.csv"


def test_multiword_client_and_epc_type():
    ident = naming.parse_send_identity("North Shore Health - Fall 2026 ePC")
    assert ident.client == "North Shore Health"
    assert ident.type == "ePC"
    assert ident.prefix == "North Shore Health Fall 2026 ePC"


@pytest.mark.parametrize("bad", ["NoSeparatorHere", "Client - no year here"])
def test_unparseable_folder_fails_loud(bad):
    with pytest.raises(ValueError):
        naming.parse_send_identity(bad)
