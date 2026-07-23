import importlib.util
import sys
from pathlib import Path

import pdfplumber
import pytest

# Load scripts/pull_reports.py as an importable module.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pull_reports.py"
_spec = importlib.util.spec_from_file_location("pull_reports", _SCRIPT)
pr = importlib.util.module_from_spec(_spec)
sys.modules["pull_reports"] = pr
_spec.loader.exec_module(pr)

from tracking import naming, sheet


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


def test_build_api_sheet_plan_missing_field_fails_loud():
    # SFMC SOAP omits null properties; NumberDelivered is absent (not "0").
    send = {"NumberSent": "100", "UniqueOpens": "10", "UniqueClicks": "5", "Subject": "Hi"}
    with pytest.raises(sheet.SheetError):
        pr.build_api_sheet_plan(send, total_opens=12, bh=2)


def test_candidate_tabs_covers_send_month_and_next():
    assert pr.candidate_tabs("2026-06-24T14:05:00") == ["June 2026", "July 2026"]
    assert pr.candidate_tabs("2026-12-30T00:00:00") == ["December 2026", "January 2027"]


def test_col_to_a1():
    assert pr.col_to_a1(0) == "A"
    assert pr.col_to_a1(24) == "Y"
    assert pr.col_to_a1(26) == "AA"


def test_find_calendar_blocks_matches_client_and_type():
    grid = [
        ["", "", "", "", "", "", "", "", "", ""],
        ["Mount Vernon School", "eQC", "", "", "", "Other Client", "eNL", "", "", ""],
    ]
    blocks = pr.find_calendar_blocks(grid, "Mount Vernon School", "eQC")
    assert blocks == [(1, 0)]


def test_find_calendar_blocks_tolerates_numeric_cells():
    # GoogleSheetsWriter reads with UNFORMATTED_VALUE, so numeric calendar cells
    # (date serials, counts) come back as int/float. A number in a name column
    # must not crash the client-key match (regression: 'int' has no .lower()).
    grid = [
        [45000, "", "", "", "", 12, "", "", "", ""],
        ["Mount Vernon School", "eQC", "", "", "", 3, "eNL", "", "", ""],
    ]
    blocks = pr.find_calendar_blocks(grid, "Mount Vernon School", "eQC")
    assert blocks == [(1, 0)]


class FakeCalWriter:
    def __init__(self, grid):
        self.grid = grid
        self.updates = []

    def get_values(self):
        return self.grid

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))


def test_mark_calendar_fills_blank_plus4_cell():
    grid = [["", "", "", "", "", "", "", "", "", ""],
            ["Mount Vernon School", "eQC", "", "", "", "", "", "", "", ""]]
    writers = {"June 2026": FakeCalWriter(grid), "July 2026": FakeCalWriter([[]])}
    ident = naming.SendIdentity(client="Mount Vernon School", season="Summer", year="2026", type="eQC")
    res = pr.mark_calendar(lambda tab: writers[tab], "2026-06-24T00:00:00", ident, "7/15 JS")
    assert res["tab"] == "June 2026" and res["cell"] == "E2" and res["status"] == "written"
    assert writers["June 2026"].updates == [(1, 4, "7/15 JS")]


def test_mark_calendar_not_found_lists_candidates():
    writers = {"June 2026": FakeCalWriter([["Nobody", "eNL"]]), "July 2026": FakeCalWriter([[]])}
    ident = naming.SendIdentity(client="Ghost", season="Summer", year="2026", type="eQC")
    res = pr.mark_calendar(lambda tab: writers[tab], "2026-06-24T00:00:00", ident, "7/15 JS")
    assert res["status"] == "not-found"
    assert res["cell"] is None
    assert res["searched_tabs"] == ["June 2026", "July 2026"]


def test_mark_calendar_ambiguous_across_both_tabs_writes_nothing():
    june = FakeCalWriter([["Mount Vernon School", "eQC", "", "", "", "", "", "", "", ""]])
    july = FakeCalWriter([["Mount Vernon School", "eQC", "", "", "", "", "", "", "", ""]])
    writers = {"June 2026": june, "July 2026": july}
    ident = naming.SendIdentity(client="Mount Vernon School", season="Summer", year="2026", type="eQC")
    res = pr.mark_calendar(lambda tab: writers[tab], "2026-06-24T00:00:00", ident, "7/15 JS")
    assert res["status"] == "ambiguous"
    assert len(res["candidates"]) == 2  # both tabs scanned before deciding
    assert {c["tab"] for c in res["candidates"]} == {"June 2026", "July 2026"}
    assert res["candidates"][0]["type"] == "eQC"  # candidates carry client/type
    assert june.updates == [] and july.updates == []  # nothing written when ambiguous


import datetime as _dt
from tracking.drafts import DraftEmail


def test_lead_scoring_filename():
    name = pr.lead_scoring_filename("sd_Example College - Lead Scoring", _dt.date(2026, 7, 15))
    assert name == "sd_Example College - Lead Scoring20260715.csv"


def test_write_lead_scoring_csv_uses_ordinal_columns(tmp_path):
    cols = ["SubscriberKey", "Score", "Class Year"]
    rows = [{"subscriberkey": "a@x", "score": "5", "class year": "2027"}]
    name = pr.write_lead_scoring_csv(tmp_path, "sd_Example College - Lead Scoring", cols, rows, _dt.date(2026, 7, 15))
    text = (tmp_path / name).read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "SubscriberKey,Score,Class Year"
    assert "a@x,5,2027" in text


def test_build_kathryn_draft_attaches_lead_file():
    lead = Path("/abs/Lead Scoring/sd_Example College - Lead Scoring20260715.csv")
    d = pr.build_kathryn_draft(_identity(), lead, hipaa=False)
    assert isinstance(d, DraftEmail)
    assert d.to == ["kathryn.baugh@pentera.com"]
    # The lead CSV is ATTACHED, by absolute path (MAX_PATH-safe).
    assert [p.name for p in d.attachments] == ["sd_Example College - Lead Scoring20260715.csv"]
    assert all(Path(p).is_absolute() for p in d.attachments)
    # No body — attachment only (the subject identifies the send).
    assert d.body == ""


def test_build_kathryn_draft_hipaa_skips():
    assert pr.build_kathryn_draft(_identity(), Path("/abs/x.csv"), hipaa=True) is None


def test_build_report_draft_blank_to_absolute_attachments(tmp_path):
    ident = _identity()
    folder = tmp_path / ident.folder_name
    folder.mkdir()
    (folder / naming.finished_pdf_name(ident)).write_bytes(b"%PDF-1.4")
    (folder / naming.finished_csv_name(ident, "Total Sent")).write_text("x")
    (folder / "sd_Example College - Lead Scoring20260715.csv").write_text("y")  # must NOT attach
    d = pr.build_report_draft(ident, folder)
    assert d.to == []
    assert d.body == ""
    assert d.subject == naming.email_subject(ident)
    names = [p.name for p in d.attachments]
    assert names[0].endswith("Engagement Tracking Report.pdf")  # PDF first
    assert any(n.endswith("- Total Sent.csv") for n in names)
    assert not any(n.startswith("sd_") for n in names)
    assert all(Path(p).is_absolute() for p in d.attachments)  # MAX_PATH-safe


class FakeSheetWriter:
    def __init__(self, grid):
        self._grid = grid
        self.updates = []

    def get_values(self):
        return self._grid

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))


class FakeDraftWriter:
    def __init__(self):
        self.created = []

    def create_draft(self, draft):
        self.created.append(draft)
        return f"draft-{len(self.created)}"


class FakeSfmc:
    """Returns fixed events/send/DE data without network."""
    def __init__(self, send, events, de_rows, de_cols, de_name="sd_Example College - Lead Scoring", de_key="KEY"):
        self._send = send; self._events = events
        self._de_rows = de_rows; self._de_cols = de_cols
        self._de_name = de_name; self._de_key = de_key

    def authenticate(self): pass
    def get_send(self, sid): return self._send
    def get_events(self, obj, sid, props):
        return self._events[{"SentEvent": "sent", "OpenEvent": "open", "ClickEvent": "click",
                             "BounceEvent": "bounce", "UnsubEvent": "unsub"}[obj]]
    def get_de_rows_rest(self, key): return self._de_rows


def _full_sheet_grid():
    """A Print Status header row with every column build_api_sheet_plan emits,
    plus one all-blank data row for 'Example College' eNL."""
    headers = ["Client", "Type", "# Total sent", "# Delivered", "# Unique opens",
               "# Unique clicks", "# Total opens", "Booklet landing page unique clicks",
               "Unique open rate %", "Unique click-through %", "Subject line"]
    return [[], [], headers, ["Example College", "eNL"] + [""] * (len(headers) - 2)]


def _deps(tmp_path, sfmc, sheet_grid=None, cal_writers=None, draft_writer=None):
    grid = sheet_grid if sheet_grid is not None else [[], [], ["Client", "Type"], []]
    return {
        "sfmc": sfmc,
        "reports_dir": tmp_path / "Completed Reports",
        "lead_dir": tmp_path / "Lead Scoring",
        "sheet_writer": FakeSheetWriter(grid),
        "calendar_writer_for_tab": (lambda tab: (cal_writers or {}).get(tab, FakeCalWriter([[]]))),
        "draft_writer": draft_writer or FakeDraftWriter(),
        # For lead scoring, patch the DE resolution to avoid SOAP in unit tests:
        "lead_de_override": ("sd_Example College - Lead Scoring", "KEY", ["SubscriberKey", "Score"]),
    }


def _send_input(**over):
    base = {"client": "Example College", "season": "Spring", "year": "2026", "type": "eNL",
            "send_id": "1", "booklet_selector": "v=enlA", "lead_scoring_de": "", "hipaa": False,
            "confirm_zero": False}
    base.update(over)
    return base


def test_require_hipaa_absent_raises():
    import pytest
    with pytest.raises(pr.SfmcError):
        pr.require_hipaa({"client": "X"})  # no hipaa key
    assert pr.require_hipaa({"hipaa": True}) is True


def test_run_send_happy_path_writes_everything(tmp_path):
    send = {"ID": "1", "Subject": "Hi", "SentDate": "2026-06-24T00:00:00", "NumberSent": "3",
            "NumberDelivered": "3", "UniqueOpens": "2", "UniqueClicks": "2",
            "HardBounces": "0", "SoftBounces": "0", "OtherBounces": "0", "Unsubscribes": "0"}
    ev = _events(sent=3, opens=2, clicks=2)
    sfmc = FakeSfmc(send, ev, de_rows=[{"subscriberkey": "a@x", "score": "5"}], de_cols=["SubscriberKey", "Score"])
    dw = FakeDraftWriter()
    deps = _deps(tmp_path, sfmc, sheet_grid=_full_sheet_grid(), draft_writer=dw)
    out = pr.run_send(_send_input(), deps, confirm_zero_ids=set())
    assert out["status"] == "complete"
    assert deps["sheet_writer"].updates  # real sheet write happened (non-dry mode)
    assert out["metrics"]["Total Sent"] == 3
    assert (tmp_path / "Completed Reports" / "Example College - Spring 2026 eNL"
            / "Example College Spring 2026 eNL - Engagement Tracking Report.pdf").exists()
    assert out["lead_scoring_file"].startswith("sd_Example College - Lead Scoring")
    assert len(dw.created) == 2  # report + kathryn


def test_run_send_needs_confirmation_skips_side_effects(tmp_path):
    send = {"ID": "1", "Subject": "Hi", "SentDate": "2026-06-24T00:00:00", "NumberSent": "5",
            "NumberDelivered": "5", "UniqueOpens": "0", "UniqueClicks": "0",
            "HardBounces": "0", "SoftBounces": "0", "OtherBounces": "0", "Unsubscribes": "0"}
    ev = _events(sent=5, opens=0, clicks=0)
    sfmc = FakeSfmc(send, ev, de_rows=[], de_cols=["SubscriberKey"])
    dw = FakeDraftWriter()
    deps = _deps(tmp_path, sfmc, draft_writer=dw)
    out = pr.run_send(_send_input(), deps, confirm_zero_ids=set())
    assert out["status"] == "needs_confirmation"
    assert out["sheet"]["status"] == "skipped"
    assert len(dw.created) == 0  # drafts blocked


def test_run_send_dry_run_computes_plan_but_writes_nothing(tmp_path):
    send = {"ID": "1", "Subject": "Hi", "SentDate": "2026-06-24T00:00:00", "NumberSent": "3",
            "NumberDelivered": "3", "UniqueOpens": "2", "UniqueClicks": "2",
            "HardBounces": "0", "SoftBounces": "0", "OtherBounces": "0", "Unsubscribes": "0"}
    ev = _events(sent=3, opens=2, clicks=2)
    sfmc = FakeSfmc(send, ev, de_rows=[{"subscriberkey": "a@x"}], de_cols=["SubscriberKey"])
    dw = FakeDraftWriter()
    deps = _deps(tmp_path, sfmc, sheet_grid=_full_sheet_grid(), draft_writer=dw)
    out = pr.run_send(_send_input(), deps, dry_run=True, confirm_zero_ids=set())
    # Nothing is mutated:
    assert not (tmp_path / "Completed Reports").exists()   # no CSV/PDF files
    assert not (tmp_path / "Lead Scoring").exists()        # no lead file
    assert dw.created == []                                # no drafts created
    assert deps["sheet_writer"].updates == []             # no real sheet write
    # ...but the full plan IS computed (spec §4):
    assert out["status"] == "dry-run"
    assert out["sheet"]["cells"]["# Total sent"]          # a real A1 ref for the matched row
    assert out["drafts"]["report"]["to"] == []
    assert out["drafts"]["report"]["attachments"]         # pdf + csvs listed
    assert out["calendar"]["status"].startswith("dry-run")
    assert out["lead_scoring_file"].startswith("sd_Example College - Lead Scoring")


def test_cfg_manifest_wins_over_env(monkeypatch):
    monkeypatch.setenv("CALENDAR_MARK_INITIALS", "JS")
    monkeypatch.setenv("CALENDAR_ID", "env-cal-id")

    # Manifest value wins over env.
    manifest = {"calendar": {"mark_initials": "AB"}}
    assert pr._cfg(manifest, "calendar", "mark_initials", "CALENDAR_MARK_INITIALS", "JS") == "AB"

    # A placeholder value ("<...>") falls back to env.
    manifest_placeholder = {"calendar": {"id": "<CALENDAR_ID>"}}
    assert pr._cfg(manifest_placeholder, "calendar", "id", "CALENDAR_ID") == "env-cal-id"

    # A missing block falls back to env.
    manifest_missing_block = {}
    assert pr._cfg(manifest_missing_block, "calendar", "mark_initials", "CALENDAR_MARK_INITIALS", "JS") == "JS"

    # Env default applies when neither manifest nor env has a value.
    monkeypatch.delenv("CALENDAR_MARK_INITIALS", raising=False)
    assert pr._cfg({}, "calendar", "mark_initials", "CALENDAR_MARK_INITIALS", "JS") == "JS"
