import importlib.util
import sys
from pathlib import Path

import pdfplumber

# Load scripts/pull_reports.py as an importable module.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pull_reports.py"
_spec = importlib.util.spec_from_file_location("pull_reports", _SCRIPT)
pr = importlib.util.module_from_spec(_spec)
sys.modules["pull_reports"] = pr
_spec.loader.exec_module(pr)

from tracking import naming


def test_manifest_round_trip(tmp_path):
    p = tmp_path / "manifest.json"
    data = {"run_id": "2026-07", "sends": [{"send_id": "1", "client": "X"}]}
    pr.save_manifest(p, data)
    assert pr.load_manifest(p) == data


def test_default_run_id_is_year_month():
    rid = pr.default_run_id()
    assert len(rid) == 7 and rid[4] == "-"  # YYYY-MM


def test_booklet_default_for_type():
    assert pr.booklet_default_for_type("eNL") == "v=enlA"
    assert pr.booklet_default_for_type("eQC") == "/requestguide"
    assert pr.booklet_default_for_type("ePC") == ""  # ePC has no default; operator supplies cID


def test_scaffold_manifest_prefills_booklet_by_type():
    m = pr.scaffold_manifest("2026-08")
    assert m["run_id"] == "2026-08"
    assert m["sends"] and m["sends"][0]["booklet_selector"] == "v=enlA"
    assert m["sends"][0]["hipaa"] is False


_SOAP_OK = b"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
 <soap:Body>
  <RetrieveResponseMsg xmlns="http://exacttarget.com/wsdl/partnerAPI">
   <OverallStatus>OK</OverallStatus>
   <RequestID>req-1</RequestID>
   <Results xsi:type="Send" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
     <ID>691994</ID><NumberSent>1000</NumberSent>
   </Results>
  </RetrieveResponseMsg>
 </soap:Body>
</soap:Envelope>"""


def test_parse_soap_reads_status_and_rows():
    status, req_id, rows = pr.parse_soap(_SOAP_OK)
    assert status == "OK"
    assert req_id == "req-1"
    assert rows == [{"ID": "691994", "NumberSent": "1000"}]


def test_build_retrieve_envelope_has_soap11_and_token_and_filter():
    env = pr.build_retrieve_envelope(
        "OpenEvent", ["SubscriberKey", "EventDate"],
        filt="<Filter>x</Filter>", token="ABC",
    )
    assert 'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"' in env
    assert '<fueloauth xmlns="http://exacttarget.com">ABC</fueloauth>' in env
    assert "<ObjectType>OpenEvent</ObjectType>" in env
    assert "<Properties>SubscriberKey</Properties>" in env
    assert "<Filter>x</Filter>" in env


def test_build_retrieve_envelope_continue_request():
    env = pr.build_retrieve_envelope("OpenEvent", ["SubscriberKey"], cont="req-1", token="ABC")
    assert "<ContinueRequest>req-1</ContinueRequest>" in env


def _soap_results(status, req_id, rows, obj="Send"):
    body = ""
    for r in rows:
        cells = "".join(f"<{k}>{v}</{k}>" for k, v in r.items())
        body += f'<Results xsi:type="{obj}">{cells}</Results>'
    rid = f"<RequestID>{req_id}</RequestID>" if req_id else ""
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><soap:Body>'  # xsi declared: fixtures use xsi:type
        '<RetrieveResponseMsg xmlns="http://exacttarget.com/wsdl/partnerAPI">'
        f"<OverallStatus>{status}</OverallStatus>{rid}{body}"
        "</RetrieveResponseMsg></soap:Body></soap:Envelope>"
    ).encode()


class FakeTransport:
    def __init__(self, soap_responses=None, get_responses=None):
        self.soap_responses = list(soap_responses or [])
        self.get_responses = list(get_responses or [])
        self.posts = []
        self.gets = []

    def post(self, url, data, headers):
        self.posts.append((url, data, headers))
        if url.endswith("/v2/token"):
            return 200, b'{"access_token":"TOK","soap_instance_url":"https://s.example.com/","rest_instance_url":"https://r.example.com/"}'
        return 200, self.soap_responses.pop(0)

    def get(self, url, headers):
        self.gets.append((url, headers))
        return 200, self.get_responses.pop(0)


def _client(transport):
    return pr.SfmcClient(
        auth_base="https://auth.example.com", soap_base="https://s.example.com",
        rest_base="https://r.example.com", client_id="id", client_secret="sec",
        transport=transport,
    )


def test_retrieve_pages_via_continue_request():
    t = FakeTransport(soap_responses=[
        _soap_results("MoreDataAvailable", "req-1", [{"SubscriberKey": "a@x"}], obj="OpenEvent"),
        _soap_results("OK", None, [{"SubscriberKey": "b@x"}], obj="OpenEvent"),
    ])
    c = _client(t); c.authenticate()
    rows = c.get_events("OpenEvent", "691994", ["SubscriberKey"])
    assert [r["SubscriberKey"] for r in rows] == ["a@x", "b@x"]
    # Second SOAP post carried a ContinueRequest.
    assert b"<ContinueRequest>req-1</ContinueRequest>" in t.posts[2][1]


def test_get_send_returns_first_row():
    t = FakeTransport(soap_responses=[_soap_results("OK", None, [{"ID": "691994", "NumberSent": "1000"}])])
    c = _client(t); c.authenticate()
    send = c.get_send("691994")
    assert send["NumberSent"] == "1000"


def test_get_de_rows_rest_merges_keys_and_values():
    body = b'{"count":1,"page":1,"pageSize":2500,"items":[{"keys":{"subscriberkey":"a@x"},"values":{"score":"5","donor id":"9"}}]}'
    t = FakeTransport(get_responses=[body])
    c = _client(t); c.authenticate()
    rows = c.get_de_rows_rest("KEY-123")
    assert rows == [{"subscriberkey": "a@x", "score": "5", "donor id": "9"}]
    assert "/data/v1/customobjectdata/key/KEY-123/rowset" in t.gets[0][0]


def test_dedup_keeps_earliest_per_subscriber():
    rows = [
        {"SubscriberKey": "a@x", "EventDate": "2026-06-02T10:00:00"},
        {"SubscriberKey": "a@x", "EventDate": "2026-06-01T09:00:00"},
        {"SubscriberKey": "b@x", "EventDate": "2026-06-01T08:00:00"},
    ]
    out = pr.dedup_by_subscriber(rows)
    keys = {r["SubscriberKey"]: r["EventDate"] for r in out}
    assert keys == {"a@x": "2026-06-01T09:00:00", "b@x": "2026-06-01T08:00:00"}


def test_usdate_formats_us_style():
    assert pr.usdate("2026-06-24T14:05:00") == "6/24/2026 2:05 PM"
    assert pr.usdate("2026-01-03T00:30:00") == "1/3/2026 12:30 AM"
    assert pr.usdate("") == ""


def test_bounce_kind_and_reason():
    assert pr.bounce_kind({"BounceCategory": "Hard bounce - Bad address"}) == "hard"
    assert pr.bounce_kind({"BounceCategory": "Soft bounce - Mailbox full"}) == "soft"
    assert pr.bounce_kind({"BounceCategory": "Technical/Other"}) == "block"
    assert pr.bounce_kind({"BounceCategory": None}) == "block"
    assert pr.bounce_reason({"BounceCategory": "Hard bounce - Bad address"}) == "Bad address"
    assert pr.bounce_reason({"BounceCategory": "Block"}) == "Block"


def test_booklet_rows_filters_by_selector_and_dedups():
    clicks = [
        {"SubscriberKey": "a@x", "EventDate": "2026-06-01T09:00:00", "URL": "https://c.giftplans.org/index.php?cID=5&v=enlA&utm=1"},
        {"SubscriberKey": "a@x", "EventDate": "2026-06-01T10:00:00", "URL": "https://c.giftplans.org/index.php?cID=5&v=enlA&utm=2"},
        {"SubscriberKey": "c@x", "EventDate": "2026-06-01T09:00:00", "URL": "https://c.giftplans.org/article-1"},
    ]
    out = pr.booklet_rows(clicks, "v=enlA")
    assert [r["SubscriberKey"] for r in out] == ["a@x"]  # deduped, article excluded


def _events(sent=2, opens=2, clicks=2, bounces=(), unsubs=0, booklet_url="v=enlA"):
    mk = lambda i, extra=None: {"SubscriberKey": f"u{i}@x", "EventDate": f"2026-06-0{i%9+1}T09:00:00", **(extra or {})}
    return {
        "sent": [mk(i) for i in range(sent)],
        "open": [mk(i) for i in range(opens)],
        "click": [mk(i, {"URL": f"https://c/index.php?cID=5&{booklet_url}"}) for i in range(clicks)],
        "bounce": list(bounces),
        "unsub": [mk(i) for i in range(unsubs)],
    }


def _identity():
    return naming.SendIdentity(client="Example College", season="Spring", year="2026", type="eNL")


def test_compute_metrics_counts_and_bh():
    m = pr.compute_metrics(_events(sent=3, opens=2, clicks=2), "v=enlA")
    assert m["counts"]["Total Sent"] == 3
    assert m["counts"]["Unique Opens"] == 2
    assert m["counts"]["Unique Clicks"] == 2
    assert m["BH"] == 2  # both clicks match v=enlA, distinct subscribers


def test_core_gate_total_sent_zero_fails():
    status, flags = pr.evaluate_core_gate({"Total Sent": 0, "Unique Opens": 5, "Unique Clicks": 5}, False)
    assert status == "failed"


def test_core_gate_zero_opens_needs_confirmation_then_released():
    status, _ = pr.evaluate_core_gate({"Total Sent": 9, "Unique Opens": 0, "Unique Clicks": 3}, False)
    assert status == "needs_confirmation"
    status2, _ = pr.evaluate_core_gate({"Total Sent": 9, "Unique Opens": 0, "Unique Clicks": 3}, True)
    assert status2 == "ok"


def test_optional_flags_for_zero_metrics():
    flags = pr.optional_flags({"Block Bounces": 0, "Unsubscribes": 4, "Request Your": 0})
    joined = " ".join(flags)
    assert "Block Bounces" in joined and "Request Your" in joined
    assert "Unsubscribes" not in joined


def test_write_engagement_csvs_data_driven(tmp_path):
    m = pr.compute_metrics(_events(sent=2, opens=2, clicks=1), "v=enlA")
    # Force one optional metric empty to prove the empty file is skipped.
    m["rows"]["Block Bounces"] = []
    files = pr.write_engagement_csvs(tmp_path, _identity(), m["rows"])
    assert "Example College Spring 2026 eNL - Total Sent.csv" in files
    assert not any("Block Bounces" in f for f in files)  # empty -> skipped
    # Header + email-as-key content check on Total Sent.
    content = (tmp_path / "Example College Spring 2026 eNL - Total Sent.csv").read_text(encoding="utf-8-sig")
    assert content.splitlines()[0] == "Subscriber Key,Email Address"
    assert "u0@x,u0@x" in content


def test_render_report_pdf_smoke(tmp_path):
    send = {
        "ID": "691866", "Subject": "Charitable Solutions", "SentDate": "2026-06-24T14:05:00",
        "NumberSent": "1086", "NumberDelivered": "1051", "UniqueOpens": "415", "UniqueClicks": "9",
        "HardBounces": "6", "SoftBounces": "23", "OtherBounces": "6", "Unsubscribes": "16",
    }
    out = tmp_path / "r.pdf"
    pr.render_report_pdf(out, _identity(), send, total_opens=675, total_clicks=25, bh=2)
    assert out.read_bytes().startswith(b"%PDF")
    with pdfplumber.open(out) as pdf:
        assert len(pdf.pages) == 1
        text = pdf.pages[0].extract_text()
    assert "Engagement Tracking Report" in text
    assert "1,051" in text and "415" in text  # delivered + unique opens present


def test_build_api_sheet_plan_values():
    send = {"NumberSent": "1086", "NumberDelivered": "1051", "UniqueOpens": "415",
            "UniqueClicks": "9", "Subject": "Hello"}
    plan = pr.build_api_sheet_plan(send, total_opens=675, bh=2)
    v = plan.values
    assert v["# Total sent"] == 1086
    assert v["# Delivered"] == 1051
    assert v["# Unique opens"] == 415
    assert v["# Unique clicks"] == 9
    assert v["# Total opens"] == 675
    assert v["Booklet landing page unique clicks"] == 2
    assert v["Subject line"] == "Hello"
    assert round(v["Unique open rate %"], 5) == round(415 / 1051, 5)


def test_build_api_sheet_plan_omits_zero_booklet():
    send = {"NumberSent": "100", "NumberDelivered": "100", "UniqueOpens": "10",
            "UniqueClicks": "0", "Subject": "Hi"}
    plan = pr.build_api_sheet_plan(send, total_opens=12, bh=0)
    assert "Booklet landing page unique clicks" not in plan.values
