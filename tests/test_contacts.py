"""Operator-maintained engagement report contact data."""

import pytest

from tracking.contacts import ContactError, load_contacts, report_contact_for
from tracking.naming import SendIdentity


def _write_contacts(path, rows):
    path.write_text(
        "client,pc_email,report_delivery_enabled\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def test_report_contact_matches_client(tmp_path):
    path = tmp_path / "contacts.csv"
    _write_contacts(path, ["Northshore College,pc@example.com,yes"])

    contact = report_contact_for(
        load_contacts(path),
        SendIdentity("Northshore College", "Fall", "2026", "eNL"),
    )

    assert contact.pc_email == "pc@example.com"
    assert contact.report_delivery_enabled is True


def test_load_contacts_accepts_whitespace_padded_headers(tmp_path):
    path = tmp_path / "contacts.csv"
    path.write_text(
        " client , pc_email , report_delivery_enabled \n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )

    contact = report_contact_for(
        load_contacts(path),
        SendIdentity("Northshore College", "Fall", "2026", "eNL"),
    )

    assert contact.pc_email == "pc@example.com"


def test_missing_contact_blocks_report_delivery(tmp_path):
    path = tmp_path / "contacts.csv"
    _write_contacts(path, ["Other College,pc@example.com,yes"])

    with pytest.raises(ContactError, match="No contact"):
        report_contact_for(load_contacts(path), SendIdentity("Northshore College", "Fall", "2026", "eNL"))


def test_duplicate_contact_blocks_report_delivery(tmp_path):
    path = tmp_path / "contacts.csv"
    _write_contacts(path, [
        "Northshore College,one@example.com,yes",
        "Northshore College,two@example.com,yes",
    ])

    with pytest.raises(ContactError, match="Multiple contacts"):
        report_contact_for(load_contacts(path), SendIdentity("Northshore College", "Fall", "2026", "eNL"))


def test_disabled_report_delivery_blocks_draft(tmp_path):
    path = tmp_path / "contacts.csv"
    _write_contacts(path, ["Northshore College,pc@example.com,no"])

    with pytest.raises(ContactError, match="disabled"):
        report_contact_for(load_contacts(path), SendIdentity("Northshore College", "Fall", "2026", "eNL"))


def test_invalid_email_blocks_report_delivery(tmp_path):
    path = tmp_path / "contacts.csv"
    _write_contacts(path, ["Northshore College,not-an-email,yes"])

    with pytest.raises(ContactError, match="Invalid PC email"):
        load_contacts(path)
