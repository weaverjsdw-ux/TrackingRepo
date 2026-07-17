import importlib.util
import sys
from pathlib import Path

# Load scripts/pull_reports.py as an importable module.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pull_reports.py"
_spec = importlib.util.spec_from_file_location("pull_reports", _SCRIPT)
pr = importlib.util.module_from_spec(_spec)
sys.modules["pull_reports"] = pr
_spec.loader.exec_module(pr)


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
