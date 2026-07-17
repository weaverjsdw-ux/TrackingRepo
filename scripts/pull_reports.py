"""pull_reports.py — manifest-driven SFMC report pull.

One saved tool that pulls a selected send from Salesforce Marketing Cloud and
produces its full report package (engagement CSVs, styled PDF, Lead Scoring
export, Print Status Report row, calendar mark, Gmail drafts). All per-run
facts live in runs/<run-id>/manifest.json; none are hardcoded.

See docs/superpowers/specs/2026-07-15-sfmc-report-pull-design.md.

Run with the repo venv:  ./.venv/Scripts/python.exe scripts/pull_reports.py <cmd>
"""
from __future__ import annotations

import csv
import datetime
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Make the installed `tracking` package importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tracking import naming, sheet

_NAVY = colors.HexColor("#1f3a5f"); _NAVY2 = colors.HexColor("#26425f"); _GREEN = colors.HexColor("#2f7d68")
_ORANGE = colors.HexColor("#c2792f"); _GRAY = colors.HexColor("#64707d"); _LINE = colors.HexColor("#d9dee5")
_ALT = colors.HexColor("#f3f5f7"); _TXT = colors.HexColor("#33404d"); _MUTE = colors.HexColor("#8892a0")

# ── booklet selector defaults by send type (a RULE; the value is confirmed
# per send in the manifest). eNL newsletters tag the booklet link v=enlA;
# eQC quarterlies use the /requestguide URL; ePC postcards vary (cID) so no
# default — the operator supplies it.
_BOOKLET_DEFAULTS = {"enl": "v=enlA", "eqc": "/requestguide"}


def default_run_id() -> str:
    """Current month as YYYY-MM (the batch label; independent of send dates)."""
    return datetime.date.today().strftime("%Y-%m")


def manifest_path(run_id: str, base: Path | None = None) -> Path:
    root = base if base is not None else Path(__file__).resolve().parents[1] / "runs"
    return root / run_id / "manifest.json"


def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_manifest(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def booklet_default_for_type(type_: str) -> str:
    return _BOOKLET_DEFAULTS.get((type_ or "").strip().lower(), "")


def scaffold_manifest(run_id: str) -> dict:
    """A template manifest with one blank send entry for the operator to fill."""
    return {
        "run_id": run_id,
        "created": datetime.date.today().isoformat(),
        "sheet": {"id": "<SHEET_ID>", "tab": "<SHEET_TAB>"},
        "calendar": {"id": "<CALENDAR_ID>", "mark_initials": "JS"},
        "sends": [
            {
                "client": "<Client Name>",
                "season": "Spring",
                "year": "2026",
                "type": "eNL",
                "send_id": "<send id>",
                "booklet_selector": booklet_default_for_type("eNL"),
                "lead_scoring_de": "",
                "hipaa": False,
                "confirm_zero": False,
            }
        ],
    }


def local_name(tag: str) -> str:
    """Strip any XML namespace: '{ns}Results' -> 'Results'."""
    return tag.rsplit("}", 1)[-1]


def build_retrieve_envelope(obj, props, filt="", cont=None, token="TOKEN"):
    p = "".join(f"<Properties>{x}</Properties>" for x in props)
    if cont:
        inner = f"<ContinueRequest>{cont}</ContinueRequest><ObjectType>{obj}</ObjectType>{p}"
    else:
        inner = f"<ObjectType>{obj}</ObjectType>{p}{filt}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<soapenv:Header><fueloauth xmlns="http://exacttarget.com">{token}</fueloauth></soapenv:Header>'
        '<soapenv:Body><RetrieveRequestMsg xmlns="http://exacttarget.com/wsdl/partnerAPI">'
        f"<RetrieveRequest>{inner}</RetrieveRequest></RetrieveRequestMsg></soapenv:Body></soapenv:Envelope>"
    )


def parse_soap(raw):
    """Return (OverallStatus, RequestID, [row dicts]) from a Retrieve response."""
    root = ET.fromstring(raw)
    status = req_id = None
    rows = []
    for el in root.iter():
        if local_name(el.tag) == "OverallStatus" and status is None:
            status = el.text
        if local_name(el.tag) == "RequestID" and req_id is None:
            req_id = el.text
    for el in root.iter():
        if local_name(el.tag) == "Results":
            rows.append({local_name(c.tag): c.text for c in el})
    return status, req_id, rows


class _UrllibTransport:
    def post(self, url, data, headers):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers=headers, method="POST"), timeout=300
            ) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, url, headers):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers, method="GET"), timeout=300
            ) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


class SfmcError(RuntimeError):
    pass


class SfmcClient:
    def __init__(self, *, auth_base, soap_base, rest_base, client_id, client_secret, transport=None):
        self.auth_base = auth_base.rstrip("/")
        self.soap_base = (soap_base or "").rstrip("/")
        self.rest_base = (rest_base or "").rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or _UrllibTransport()
        self.token = None
        self.soap_url = None
        self.rest_url = None

    def authenticate(self):
        body = json.dumps({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        st, raw = self.transport.post(
            f"{self.auth_base}/v2/token", body, {"Content-Type": "application/json"}
        )
        if st != 200:
            raise SfmcError(f"SFMC auth failed (HTTP {st}): {raw[:200]!r}")
        tok = json.loads(raw)
        self.token = tok["access_token"]
        self.soap_url = (tok.get("soap_instance_url") or self.soap_base).rstrip("/")
        self.rest_url = (tok.get("rest_instance_url") or self.rest_base).rstrip("/")

    def _soap_call(self, obj, props, filt="", cont=None):
        env = build_retrieve_envelope(obj, props, filt=filt, cont=cont, token=self.token)
        st, raw = self.transport.post(
            f"{self.soap_url}/Service.asmx", env.encode(),
            {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "Retrieve"},
        )
        if st != 200:
            raise SfmcError(f"SOAP {obj} failed (HTTP {st}): {raw[:200]!r}")
        return parse_soap(raw)

    def retrieve(self, obj, props, filt=""):
        status, req_id, rows = self._soap_call(obj, props, filt=filt)
        guard = 0
        while status == "MoreDataAvailable" and req_id and guard < 2000:
            guard += 1
            status, req_id, more = self._soap_call(obj, props, cont=req_id)
            rows += more
        return rows

    def get_send(self, send_id):
        filt = (
            '<Filter xsi:type="SimpleFilterPart"><Property>ID</Property>'
            f"<SimpleOperator>equals</SimpleOperator><Value>{send_id}</Value></Filter>"
        )
        rows = self.retrieve("Send", [
            "ID", "EmailName", "Subject", "SentDate", "NumberSent", "NumberDelivered",
            "HardBounces", "SoftBounces", "OtherBounces", "UniqueOpens", "UniqueClicks", "Unsubscribes",
        ], filt)
        if not rows:
            raise SfmcError(f"No Send found for ID {send_id}.")
        return rows[0]

    def get_events(self, obj, send_id, props):
        filt = (
            '<Filter xsi:type="SimpleFilterPart"><Property>SendID</Property>'
            f"<SimpleOperator>equals</SimpleOperator><Value>{send_id}</Value></Filter>"
        )
        return self.retrieve(obj, props, filt)

    def get_de_rows_rest(self, customer_key, page_size=2500):
        out = []
        page = 1
        while True:
            url = (f"{self.rest_url}/data/v1/customobjectdata/key/{customer_key}/rowset"
                   f"?$page={page}&$pageSize={page_size}")
            st, raw = self.transport.get(url, {"Authorization": f"Bearer {self.token}"})
            if st != 200:
                raise SfmcError(f"DE rowset failed (HTTP {st}) for {customer_key}: {raw[:200]!r}")
            payload = json.loads(raw)
            for item in payload.get("items", []):
                row = dict(item.get("keys", {}))
                row.update(item.get("values", {}))
                out.append(row)
            count = payload.get("count", len(out))
            if page * page_size >= count or not payload.get("items"):
                break
            page += 1
        return out


def dedup_by_subscriber(rows):
    """Keep the earliest EventDate row per SubscriberKey (stable)."""
    seen = set()
    out = []
    for r in sorted(rows, key=lambda x: x.get("EventDate") or ""):
        k = r.get("SubscriberKey")
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def usdate(iso):
    """ISO 'YYYY-MM-DDThh:mm:ss' -> US 'M/D/YYYY h:mm AM/PM' (matches UI export)."""
    if not iso:
        return ""
    d, _, t = iso.partition("T")
    try:
        y, mo, da = d.split("-")
        hh = int(t.split(":")[0])
        mm = t.split(":")[1][:2]
    except Exception:
        return iso
    ap = "AM" if hh < 12 else "PM"
    h12 = hh % 12 or 12
    return f"{int(mo)}/{int(da)}/{int(y)} {h12}:{mm} {ap}"


def bounce_kind(row):
    """Hard/Soft explicit; everything else -> block (== Send.OtherBounces line)."""
    c = (row.get("BounceCategory") or "").lower()
    return "hard" if c.startswith("hard") else "soft" if c.startswith("soft") else "block"


def bounce_reason(row):
    c = row.get("BounceCategory") or ""
    return c.split(" - ", 1)[1] if " - " in c else c


def booklet_rows(clicks, selector):
    """Deduped clicks whose URL contains the per-send booklet selector."""
    if not selector:
        return []
    matched = [r for r in clicks if selector.lower() in (r.get("URL") or "").lower()]
    return dedup_by_subscriber(matched)


# CSV column layout per metric: (header, row-builder). SubscriberKey doubles as
# Email Address in this account (spec §5.2). Booklet reuses the clicks layout.
_CORE = ("Total Sent", "Unique Opens", "Unique Clicks")
_OPTIONAL = ("Hard Bounces", "Soft Bounces", "Block Bounces", "Unsubscribes", "Request Your")

_CSV_LAYOUT = {
    "Total Sent": (["Subscriber Key", "Email Address"],
                   lambda r: [r["SubscriberKey"], r["SubscriberKey"]]),
    "Unique Opens": (["Subscriber Key", "Email Address", "Time Opened"],
                     lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate"))]),
    "Unique Clicks": (["Subscriber Key", "Email Address", "Click-Through Time", "Link Clicked"],
                      lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate")), r.get("URL")]),
    "Request Your": (["Subscriber Key", "Email Address", "Click-Through Time", "Link Clicked"],
                     lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate")), r.get("URL")]),
    "Unsubscribes": (["Subscriber Key", "Email Address", "Unsubscribed Time"],
                     lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate"))]),
}
_BOUNCE_HEADER = ["Subscriber Key", "Email Address", "Undelivered Time", "Bounce Reason", "Bounce Description"]
_BOUNCE_ROW = lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate")),
                         bounce_reason(r), r.get("SMTPReason")]
for _b in ("Hard Bounces", "Soft Bounces", "Block Bounces"):
    _CSV_LAYOUT[_b] = (_BOUNCE_HEADER, _BOUNCE_ROW)


def compute_metrics(events, booklet_selector):
    sent = dedup_by_subscriber(events["sent"])
    opens = dedup_by_subscriber(events["open"])
    clicks_raw = events["click"]
    clicks = dedup_by_subscriber(clicks_raw)
    booklet = booklet_rows(clicks_raw, booklet_selector)
    unsub = dedup_by_subscriber(events["unsub"])
    bounces = {kind: dedup_by_subscriber([r for r in events["bounce"] if bounce_kind(r) == kind])
               for kind in ("hard", "soft", "block")}
    rows = {
        "Total Sent": sent,
        "Unique Opens": opens,
        "Unique Clicks": clicks,
        "Request Your": booklet,
        "Hard Bounces": bounces["hard"],
        "Soft Bounces": bounces["soft"],
        "Block Bounces": bounces["block"],
        "Unsubscribes": unsub,
    }
    counts = {k: len(v) for k, v in rows.items()}
    return {
        "rows": rows,
        "counts": counts,
        "BH": len(booklet),
        "Total Opens": len(events["open"]),
        "Total Clicks": len(events["click"]),
    }


def evaluate_core_gate(counts, confirm_zero):
    if counts.get("Total Sent", 0) == 0:
        return "failed", ["Total Sent = 0 — pull looks broken; refusing to proceed."]
    zeros = [m for m in ("Unique Opens", "Unique Clicks") if counts.get(m, 0) == 0]
    if zeros and not confirm_zero:
        return "needs_confirmation", [f"{m} = 0 — confirm before writing side effects." for m in zeros]
    return "ok", [f"{m} = 0 — confirmed by operator." for m in zeros] if zeros else []


def optional_flags(counts):
    return [f"{m}: 0 — no file written" for m in _OPTIONAL if counts.get(m, 0) == 0]


def write_engagement_csvs(folder, identity, metric_rows):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for metric, (header, build_row) in _CSV_LAYOUT.items():
        rows = metric_rows.get(metric) or []
        if not rows:
            continue  # data-driven: skip empty/absent metrics
        desc = naming.REQUEST_FILE_DESCRIPTION if metric == "Request Your" else metric
        name = naming.finished_csv_name(identity, desc)
        with open(folder / name, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(build_row(r) for r in rows)
        written.append(name)
    return written


def _sty(**k):
    k.setdefault("leading", k.get("fontSize", 10) * 1.25)
    return ParagraphStyle("x", **k)


def _san(t):
    t = t or ""
    for a, b in [("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("…", "..."), (" ", " ")]:
        t = t.replace(a, b)
    return t.strip()


def _kpi(num, label):
    p = ParagraphStyle("k", textColor=colors.white, alignment=1, leading=20)
    return Paragraph(f'<font size="17"><b>{num}</b></font><br/><font size="7.5">{label}</font>', p)


def _perf_table(rows, indent_labels=()):
    t = Table([[r[0], r[1]] for r in rows], colWidths=[1.9 * inch, 1.05 * inch])
    ts = [('FONTSIZE', (0, 0), (-1, -1), 9.5), ('TEXTCOLOR', (0, 0), (0, -1), _GRAY),
          ('TEXTCOLOR', (1, 0), (1, -1), _TXT), ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
          ('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('LINEBELOW', (0, 0), (-1, -2), 0.5, _LINE),
          ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]
    for i in indent_labels:
        ts.append(('LEFTPADDING', (0, i), (0, i), 16))
    t.setStyle(TableStyle(ts))
    return t


def render_report_pdf(path, identity, send, total_opens, total_clicks, bh):
    def n(x):
        try:
            return int(x or 0)
        except (TypeError, ValueError):
            return 0

    sent = n(send.get("NumberSent")); deliv = n(send.get("NumberDelivered"))
    uo = n(send.get("UniqueOpens")); uc = n(send.get("UniqueClicks")); topen = n(total_opens)
    hard = n(send.get("HardBounces")); soft = n(send.get("SoftBounces")); block = n(send.get("OtherBounces"))
    unsub = n(send.get("Unsubscribes")); tb = hard + soft + block
    dr = deliv / sent * 100 if sent else 0
    orr = uo / deliv * 100 if deliv else 0
    ctr = uc / deliv * 100 if deliv else 0
    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.45 * inch)
    E = []
    E.append(Paragraph("Engagement Tracking Report",
                       _sty(fontName="Helvetica-Bold", fontSize=22, textColor=_NAVY, spaceAfter=2)))
    E.append(Paragraph(_san(f"{identity.client}  -  {identity.season} {identity.year} {identity.type}"),
                       _sty(fontName="Helvetica", fontSize=10.5, textColor=_MUTE, spaceAfter=12)))
    meta = Table([["Job ID", send.get("ID")], ["Subject", _san(send.get("Subject"))],
                  ["Date Sent", (send.get("SentDate") or "").replace("T", " ")],
                  ["Total Sent", f"{sent:,}"], ["Total Not Sent", "0"]], colWidths=[1.4 * inch, 4.6 * inch])
    meta.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 9.5), ('TEXTCOLOR', (0, 0), (0, -1), _GRAY),
        ('TEXTCOLOR', (1, 0), (1, -1), _TXT), ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, _LINE), ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5)]))
    E.append(meta); E.append(Spacer(1, 9))
    tiles = Table([[_kpi(f"{deliv:,}", f"Delivered - {dr:.3f}%"), _kpi(f"{uo:,}", f"Unique Opens - {orr:.3f}%"),
                    _kpi(f"{uc:,}", f"Unique Clicks - {ctr:.3f}%"), _kpi(f"{unsub:,}", "Unsubscribes")]],
                  colWidths=[1.62 * inch] * 4, rowHeights=[0.54 * inch])
    tiles.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, 0), _NAVY2), ('BACKGROUND', (1, 0), (1, 0), _GREEN),
        ('BACKGROUND', (2, 0), (2, 0), _ORANGE), ('BACKGROUND', (3, 0), (3, 0), _GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6)]))
    E.append(tiles); E.append(Spacer(1, 11))
    hdr = _sty(fontName="Helvetica-Bold", fontSize=12, textColor=_NAVY, spaceAfter=5)
    left = [Paragraph("Send Performance", hdr), _perf_table(
        [("Delivery Rate", f"{dr:.3f}%"), ("Total Bounces", f"{tb:,}"), ("Hard Bounce", f"{hard:,}"),
         ("Soft Bounce", f"{soft:,}"), ("Block Bounce", f"{block:,}"), ("Delivered", f"{deliv:,}")],
        indent_labels=(2, 3, 4))]
    right = [Paragraph("Open Performance", hdr), _perf_table(
        [("Open Rate", f"{orr:.3f}%"), ("Total Opens", f"{topen:,}"), ("Unique Opens", f"{uo:,}"),
         ("Total Clicks", f"{total_clicks:,}"), ("Unique Clicks", f"{uc:,}"), ("Booklet clicks (BH)", f"{bh:,}")])]
    two = Table([[left, right]], colWidths=[3.15 * inch, 3.15 * inch])
    two.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (0, 0), 0),
                             ('LEFTPADDING', (1, 0), (1, 0), 18)]))
    E.append(two); E.append(Spacer(1, 11))
    E.append(Paragraph("Inbox Activity", hdr))
    ia = [["Metric", "Total", "Unique"], ["Opens", f"{topen:,}", f"{uo:,}"], ["Clicks", f"{total_clicks:,}", f"{uc:,}"],
          ["Forwards", "0", "0"], ["Conversions", "0", "0"], ["Unsubscribes", "-", f"{unsub:,}"]]
    iat = Table(ia, colWidths=[3.0 * inch, 1.65 * inch, 1.65 * inch])
    iast = [('BACKGROUND', (0, 0), (-1, 0), _NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9.5),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'), ('TEXTCOLOR', (0, 1), (-1, -1), _TXT),
            ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, _LINE)]
    for r in range(2, 6, 2):
        iast.append(('BACKGROUND', (0, r), (-1, r), _ALT))
    iat.setStyle(TableStyle(iast)); E.append(iat); E.append(Spacer(1, 9))
    E.append(Paragraph(f"Unengaged Subscribers (of {deliv:,} delivered)", hdr))
    E.append(_perf_table([("Did not open", f"{deliv - uo:,}"), ("Did not click", f"{deliv - uc:,}")]))
    E.append(Spacer(1, 9))
    d = Drawing(430, 84); bc = HorizontalBarChart()
    bc.x = 70; bc.y = 4; bc.height = 72; bc.width = 300
    bc.data = [[sent, deliv, uo, uc]]; bc.categoryAxis.categoryNames = ["Sent", "Delivered", "Opened", "Clicked"]
    bc.categoryAxis.labels.fontSize = 8; bc.valueAxis.visible = False
    bc.valueAxis.valueMin = 0; bc.valueAxis.valueMax = sent * 1.15 if sent else 1
    bc.bars[0].fillColor = _NAVY; bc.bars.strokeColor = None; bc.barWidth = 12; bc.groupSpacing = 7
    bc.barLabelFormat = '%d'; bc.barLabels.fontSize = 8; bc.barLabels.dx = 4
    bc.barLabels.boxAnchor = 'w'; bc.barLabels.fillColor = _TXT
    d.add(bc)
    foot = Paragraph("Generated by engagement-tracker from Marketing Cloud API data (Send object + open/click "
                     "tracking). Bounce sub-split may differ by a few from the UI; the total is exact. Not "
                     "ExactTarget's document.", _sty(fontName="Helvetica-Oblique", fontSize=7.5, textColor=_MUTE))
    E.append(KeepTogether([Paragraph("Delivery Funnel", hdr), d, Spacer(1, 8), foot]))
    doc.build(E)


def build_api_sheet_plan(send, total_opens, bh):
    """Map SFMC aggregates directly onto the Print Status Report headers.

    Deliberately does NOT reuse sheet.build_sheet_plan (that is PDF/file-count
    oriented, spec §5.5). Only sheet.write_send's row-match/fill-blank logic is
    reused, on the SheetPlan built here.
    """
    def n(x):
        try:
            return int(x or 0)
        except (TypeError, ValueError):
            return 0

    sent = n(send.get("NumberSent")); deliv = n(send.get("NumberDelivered"))
    uo = n(send.get("UniqueOpens")); uc = n(send.get("UniqueClicks"))
    plan = sheet.SheetPlan()
    plan.values["# Total sent"] = sent
    plan.values["# Delivered"] = deliv
    plan.values["# Unique opens"] = uo
    plan.values["# Unique clicks"] = uc
    plan.values["# Total opens"] = int(total_opens)
    if bh:
        plan.values["Booklet landing page unique clicks"] = int(bh)
    else:
        plan.flags.append("BH = 0 — booklet cell omitted; confirm the booklet link.")
    if deliv:
        plan.values["Unique open rate %"] = round(uo / deliv, 5)
        plan.values["Unique click-through %"] = round(uc / deliv, 5)
    subject = send.get("Subject")
    if subject:
        plan.values["Subject line"] = subject
    return plan


_CAL_NAME_COLS = (0, 5, 10, 15, 20)


def _cal_key(s):
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def candidate_tabs(send_date_iso):
    """Report task lags the send by ~2 weeks, so search the send month + next."""
    d = datetime.date.fromisoformat((send_date_iso or "").split("T")[0])
    nxt = (d.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    return [d.strftime("%B %Y"), nxt.strftime("%B %Y")]


def col_to_a1(col):
    s = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        s = chr(65 + rem) + s
    return s


def _cell(grid, r, c):
    row = grid[r] if r < len(grid) else []
    return row[c] if c < len(row) else ""


def find_calendar_blocks(grid, client, type_):
    ck, tk = _cal_key(client), _cal_key(type_)
    out = []
    for r in range(len(grid)):
        for nc in _CAL_NAME_COLS:
            name = _cal_key(_cell(grid, r, nc))
            if name and ck in name and tk and tk == _cal_key(_cell(grid, r, nc + 1)):
                out.append((r, nc))
    return out


def mark_calendar(writer_for_tab, send_date_iso, identity, mark, fill_blanks_only=True):
    """Scan ALL candidate tabs fully, collect every client+type block, THEN decide.
    Never writes until we know there is exactly one match across both months.
    Candidates carry tab/row/client/type so status/dry-run reporting is useful."""
    tabs = candidate_tabs(send_date_iso)
    matches = []      # (tab, writer, grid, row, name_col)
    candidates = []   # operator-facing block descriptors
    for tab in tabs:
        writer = writer_for_tab(tab)
        grid = writer.get_values()
        for (r, nc) in find_calendar_blocks(grid, identity.client, identity.type):
            matches.append((tab, writer, grid, r, nc))
            candidates.append({
                "tab": tab, "row": r + 1, "name_col": col_to_a1(nc),
                "client": _cell(grid, r, nc), "type": _cell(grid, r, nc + 1),
            })
    result = {"tab": None, "cell": None, "status": None,
              "candidates": candidates, "searched_tabs": tabs}
    if len(matches) == 1:
        tab, writer, grid, r, nc = matches[0]
        mark_col = nc + 4
        result.update(tab=tab, cell=f"{col_to_a1(mark_col)}{r + 1}")
        if str(_cell(grid, r, mark_col)).strip() and fill_blanks_only:
            result["status"] = "already"
        else:
            writer.update_cell(r, mark_col, mark)
            result["status"] = "written"
    elif len(matches) > 1:
        result["status"] = "ambiguous"
    else:
        result["status"] = "not-found"
    return result
