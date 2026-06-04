"""Generate the committed synthetic golden fixtures.

NO real PII is ever committed (BRIEF §0). These fixtures are fully synthetic but
carry the *real* ExactTarget header signatures and deliberately bake in the
real-world parser quirks the brief calls out (BRIEF §3 "Parsing robustness"):

  * a UTF-8 BOM (export_1001),
  * a quoted field with an embedded comma (FullName2 "Smith, Jr."),
  * an embedded newline inside a quoted field (Account Name),
  * a name with diacritics ("José Núñez").

Run:  python tests/fixtures/_generate_synthetic.py
Outputs to:  tests/fixtures/synthetic/Northshore College - Fall 2026 eNL/

Known-correct golden values are asserted in tests/test_golden_synthetic.py and
mirrored in this module's GOLDEN dict for documentation.
"""

from __future__ import annotations

import csv
from pathlib import Path

OUT = Path(__file__).parent / "synthetic" / "Northshore College - Fall 2026 eNL"

BASE = [
    "Subscriber Key", "Email Address", "First Name", "Last Name", "FullName2",
    "State", "Birth date", "Age", "Gender", "Marital Status", "Class Year",
    "Donor ID", "Client Defined 1", "Client Defined 2", "Client Defined 3",
    "Monthly Article URL for Web Words", "Account Name", "SF Status",
]
OPENS = BASE + ["Time Opened"]
CLICKS = BASE + ["Click-Through Time", "Link Clicked"]
BOUNCE = BASE + ["Undelivered Time", "Bounce Reason", "Bounce Description"]
UNSUB = BASE + ["Unsubscribed Time"]
LEAD = [
    "SubscriberKey", "CreateDate", "Score", "Requested Booklet", "First Name",
    "Last Name", "Full Name", "State", "Birth date", "Age", "Class Year",
    "Donor ID", "Gender", "Client Defined 1", "Client Defined 2", "Client Defined 3",
]

_HOST = "https://northshore.giftplans.org"
LANDING = f"{_HOST}/enewsletter/fall-newsletter-2026"
ARTICLE1 = f"{LANDING}/article-1"
ARTICLE2 = f"{LANDING}/article-2"
REQUESTGUIDE = f"{_HOST}/RequestGuide"
UNSUB_LINK = "http://click.email.giftplans.org/unsub_center.aspx"
VIEW_LINK = "https://view.email.giftplans.org/"
_UTM = "?utm_source=sfmc&utm_medium=email&sfmc_id=42"


def _row(header: list[str], **vals: str) -> dict[str, str]:
    return {col: vals.get(col.replace(" ", "_"), "") for col in header}


def _write(name: str, header: list[str], rows: list[dict[str, str]], *, bom: bool) -> None:
    enc = "utf-8-sig" if bom else "utf-8"
    with open(OUT / name, "w", newline="", encoding=enc) as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # --- Total Sent: 6 rows, with all four baked quirks. BOM on this file. ---
    sent = [
        _row(BASE, Subscriber_Key="s1", Email_Address="a@x.org", First_Name="Ann",
             Last_Name="Lee", FullName2="Ms. Lee", SF_Status="ok"),
        # quoted embedded comma:
        _row(BASE, Subscriber_Key="s2", Email_Address="b@x.org", First_Name="Bob",
             Last_Name="Smith", FullName2="Smith, Jr.", Account_Name="Acme, Inc."),
        # diacritics:
        _row(BASE, Subscriber_Key="s3", Email_Address="c@x.org", First_Name="José",
             Last_Name="Núñez", FullName2="Sr. Núñez"),
        # embedded newline inside a quoted field:
        _row(BASE, Subscriber_Key="s4", Email_Address="d@x.org", First_Name="Dee",
             Last_Name="Park", Account_Name="Line one\nline two"),
        _row(BASE, Subscriber_Key="s5", Email_Address="e@x.org", First_Name="Eve"),
        _row(BASE, Subscriber_Key="s6", Email_Address="f@x.org", First_Name="Foo"),
    ]
    _write("export_1001.csv", BASE, sent, bom=True)

    # --- Unique Opens: 4 rows ---
    opens = [_row(OPENS, Subscriber_Key=f"s{i}", Time_Opened="10/1/2026 9:00")
             for i in range(1, 5)]
    _write("export_1002.csv", OPENS, opens, bom=False)

    # --- Unique Clicks master: 9 rows, 6 distinct links (utm params on some) ---
    def click(link: str) -> dict[str, str]:
        return _row(CLICKS, Subscriber_Key="sx", Link_Clicked=link,
                    Click_Through_Time="10/1/2026 9:05")
    clicks = (
        [click(ARTICLE1 + _UTM), click(ARTICLE1)]            # article-1 x2
        + [click(ARTICLE2)]                                   # article-2 x1
        + [click(LANDING + _UTM), click(LANDING), click(LANDING)]  # landing x3
        + [click(REQUESTGUIDE)]                               # secondary CTA x1
        + [click(UNSUB_LINK), click(VIEW_LINK)]               # system x2
    )
    _write("export_1003.csv", CLICKS, clicks, bom=False)

    # --- Booklet single-link file: 3 rows, only the landing page ---
    booklet = [click(LANDING + _UTM), click(LANDING), click(LANDING)]
    _write("export_1004.csv", CLICKS, booklet, bom=False)

    # --- Hard Bounces: 2 rows ---
    bounce = [_row(BOUNCE, Subscriber_Key=f"s{i}", Bounce_Reason="Inactive Account",
                   Bounce_Description="Address is temporarily unavailable")
              for i in range(1, 3)]
    _write("export_1005.csv", BOUNCE, bounce, bom=False)

    # --- Unsubscribes: 1 row ---
    unsub = [_row(UNSUB, Subscriber_Key="s1", Unsubscribed_Time="10/2/2026 8:00")]
    _write("export_1006.csv", UNSUB, unsub, bom=False)

    # --- Lead scoring: 2 rows (out of pipeline; present to prove it is ignored) ---
    lead = [_row(LEAD, SubscriberKey=f"s{i}", Score="85", Requested_Booklet="Y")
            for i in range(1, 3)]
    _write("sd_Northshore College - Lead Scoring20260901.csv", LEAD, lead, bom=False)

    _write_overview_pdf("Job_770001_Overview_20260901.pdf")

    print(f"Wrote synthetic fixtures to {OUT}")


def _write_overview_pdf(name: str) -> None:
    """Generate a minimal overview PDF whose text triggers content-based PDF
    identification. reportlab is a generate-time-only dependency (the committed
    PDF needs nothing at test time)."""
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        print(f"SKIP {name}: reportlab not installed (regenerate with `pip install reportlab`)")
        return
    # Mirror the real overview's Summary layout so overview.parse_summary is
    # tested against the same shapes in CI. Counts match the synthetic files.
    lines = [
        "Summary",
        "Engagement Tracking Report Overview",
        "Total Sent:6",
        "Hard Bounce: 2",
        "Soft Bounce: 0",
        "Block Bounce: 0",
        "Delivered: 4",
        "Total Opens: 5",
        "Unique Opens:4",
        "Total Unique",
        "Opens 5 4",
        "Clicks 9 9",
        "Unsubscribes - 1",
    ]
    c = canvas.Canvas(str(OUT / name))
    y = 760
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()


# Documented known-correct values (mirrored as asserts in the test).
GOLDEN = {
    "Total Sent": 6,
    "Unique Opens": 4,
    "Unique Clicks": 9,        # master row count
    "Unique Clicks distinct": 6,
    "Booklet rows": 3,
    "Hard Bounces": 2,
    "Unsubscribes": 1,
    "Lead Scoring rows": 2,
    "BH": 3,                   # landing page via common-parent tiebreak
}

if __name__ == "__main__":
    main()
