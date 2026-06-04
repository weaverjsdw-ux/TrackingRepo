"""Phase 4 — clients.csv lookup + lead-scoring parse/route (no sends)."""

import pytest

from tracking import clients, leadscoring
from tracking.intake import Attachment, EmailMessage

CSV = (
    "client,hipaa,pc_name,pc_email\n"
    "# comment line\n"
    "Bradley University,no,,\n"
    "Mercy Hospital,yes,Dana Coordinator,dana@mercy.org\n"
)


def _clients(tmp_path):
    p = tmp_path / "clients.csv"
    p.write_text(CSV, encoding="utf-8")
    return clients.load_clients(p)


def _ls_email(subject, sd_name="sd_Bradley University - Lead Scoring20260514.csv"):
    return EmailMessage("m1", subject, (Attachment(sd_name, b"SubscriberKey,Score\n1,5\n"),),
                        "lead scoring attached")


# --- clients.csv ---

def test_load_and_lookup(tmp_path):
    cm = _clients(tmp_path)
    assert clients.lookup(cm, "Bradley University").hipaa is False
    mercy = clients.lookup(cm, "mercy hospital")  # case-insensitive
    assert mercy.hipaa is True and mercy.pc_email == "dana@mercy.org"


def test_unknown_client_fails_loud(tmp_path):
    with pytest.raises(clients.ClientNotFound):
        clients.lookup(_clients(tmp_path), "Nobody College")


def test_bad_hipaa_value_fails_loud(tmp_path):
    p = tmp_path / "clients.csv"
    p.write_text("client,hipaa,pc_name,pc_email\nX,maybe,,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hipaa"):
        clients.load_clients(p)


def test_hipaa_without_pc_email_fails_loud(tmp_path):
    p = tmp_path / "clients.csv"
    p.write_text("client,hipaa,pc_name,pc_email\nX,yes,Someone,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pc_email"):
        clients.lookup(clients.load_clients(p), "X")


# --- lead-scoring parse ---

def test_parse_lead_scoring_email():
    ls = leadscoring.parse_lead_scoring_email(_ls_email("Lead Scoring - Bradley University Spring 2026 eNL"))
    assert ls is not None
    assert ls.identity.client == "Bradley University"
    assert ls.identity.type == "eNL"
    assert ls.sd_attachment.filename.startswith("sd_")


def test_parse_lowercase_and_eqc():
    ls = leadscoring.parse_lead_scoring_email(_ls_email("Lead scoring - HIAS Fdn Spring 2026 eQC"))
    assert ls.identity.client == "HIAS Fdn" and ls.identity.type == "eQC"


def test_parse_rejects_non_leadscoring_or_missing_sd():
    assert leadscoring.parse_lead_scoring_email(_ls_email("Weekly update")) is None
    no_sd = EmailMessage("m2", "Lead Scoring - Bradley University Spring 2026 eNL",
                         (Attachment("report.pdf", b"x"),), "")
    assert leadscoring.parse_lead_scoring_email(no_sd) is None


# --- routing decision ---

def test_route_non_hipaa_to_kathryn(tmp_path):
    ls = leadscoring.parse_lead_scoring_email(_ls_email("Lead Scoring - Bradley University Spring 2026 eNL"))
    r = leadscoring.route(ls, _clients(tmp_path))
    assert r.kind == "notify-kathryn"
    assert leadscoring.KATHRYN_EMAIL == "kathryn.baugh@pentera.com"


def test_route_hipaa(tmp_path):
    ls = leadscoring.parse_lead_scoring_email(
        _ls_email("Lead Scoring - Mercy Hospital Spring 2026 eNL", "sd_Mercy Hospital - Lead Scoring20260514.csv"))
    r = leadscoring.route(ls, _clients(tmp_path))
    assert r.kind == "hipaa" and r.client_info.pc_email == "dana@mercy.org"


def test_route_unknown_client_fails_loud(tmp_path):
    ls = leadscoring.parse_lead_scoring_email(
        _ls_email("Lead Scoring - Nobody College Spring 2026 eNL", "sd_Nobody College - Lead Scoring.csv"))
    with pytest.raises(clients.ClientNotFound):
        leadscoring.route(ls, _clients(tmp_path))
