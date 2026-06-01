"""Regression guard: the DEPRECATED §2.E steps must never be reintroduced
(BRIEF §2.E "DO NOT BUILD" -- Click Activity Tab / Link View / per-cID exports /
cID= filenames). If anyone adds them, this test fails loudly."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tracking"

# Tokens that would indicate the retired cID/Click-Activity workflow crept back.
FORBIDDEN = ["cID=", "Click Activity", "Link View", "per-cID"]


def test_no_deprecated_cid_click_activity_logic():
    offenders = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            # Allow the token inside an explicit "do not build" comment reference.
            for lineno, line in enumerate(text.splitlines(), 1):
                if token in line and "BRIEF §2.E" not in line and "DEPRECATED" not in line:
                    offenders.append(f"{py.name}:{lineno}: {token!r} -> {line.strip()}")
    assert not offenders, "Deprecated §2.E logic reintroduced:\n" + "\n".join(offenders)
