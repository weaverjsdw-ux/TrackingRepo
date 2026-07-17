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
