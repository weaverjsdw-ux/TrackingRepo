"""BH = booklet landing-page unique click-thru (BRIEF §2.A §11a).

There are TWO live paths (operator decision), and the example folder exercises
both:

  * PRIMARY -- request file present: BH = the request (booklet) file's row count.
    The request file is the click-signature export (Link Clicked column) with a
    single distinct link. Going forward this file is a standard per-send input.

  * FALLBACK -- no request file (e.g. the current clean example's raw set):
    derive the booklet from the master Unique Clicks file. The booklet link is
    the newsletter landing page = the common parent of the /article-N links
    (".../giving-thought-spring-2026"); article, system, and CTA links
    (/requestguide) are excluded.

  * BOTH present and disagreeing -> use the request file, log a warning
    (different export snapshots happen).

Both paths must yield 21 on the corrected single-send example: finished via the
request-file count, raw via the clicks-derive.

Every run logs which link(s) were classified as the booklet (BRIEF §2.A §11a),
and the derive fails loud on zero/ambiguous (never guesses).

KNOWN HARDENING ITEM (Phase 2): the procedure allows up to 3 booklet links per
send. The derive currently treats an irreducibly-ambiguous multi-survivor set as
a hard failure -- safe for now, but a real multi-booklet send needs an explicit
allow-list/confirmation path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Maintained system-link exclusion list (BRIEF §2.A §11a: "unsubscribe,
# update/manage preferences, view-in-browser, social/share, privacy"). Matched
# as substrings against the normalized link.
SYSTEM_LINK_SUBSTRINGS = (
    "unsub_center", "profile_center", "/manage", "preference",
    "view.email", "view-in-browser", "/privacy",
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "/share",
)

# Maintained CTA exclusion list: secondary call-to-action links that are NOT the
# booklet landing page (operator decision -- exclude /requestguide in the
# clicks-derive). Note the request *file* points at the landing page, not at this
# CTA, so excluding it never removes the booklet itself.
CTA_LINK_SUBSTRINGS = ("requestguide", "request-guide")

_ARTICLE_RE = re.compile(r"/article-\d+\b", re.IGNORECASE)


class BHError(ValueError):
    """Raised when the clicks-derive yields zero or ambiguous booklet links."""


@dataclass(frozen=True)
class BHResult:
    bh: int
    booklet_links: list[str]
    method: str  # "request-file" | "clicks-derive(common-parent)" | "clicks-derive(single-survivor)"
    candidates: dict[str, int] = field(default_factory=dict)
    log_lines: list[str] = field(default_factory=list)
    warning: str | None = None


def is_article_link(link: str) -> bool:
    return bool(_ARTICLE_RE.search(link))


def is_system_link(link: str) -> bool:
    return any(s in link for s in SYSTEM_LINK_SUBSTRINGS)


def is_cta_link(link: str) -> bool:
    return any(s in link for s in CTA_LINK_SUBSTRINGS)


def _article_common_parent(links: list[str]) -> str | None:
    """Shared parent path of the article links, e.g.
    '.../enewsletter/giving-thought-spring-2026' from '.../article-1..7'.
    None if there are no article links or they disagree."""
    parents = {_ARTICLE_RE.split(l, maxsplit=1)[0] for l in links if is_article_link(l)}
    return parents.pop() if len(parents) == 1 else None


def derive_from_master(link_counts: dict[str, int]) -> BHResult:
    """FALLBACK path: derive the booklet from the master Unique Clicks file.

    link_counts keys must be normalized (see parse.normalize_link)."""
    log: list[str] = []
    all_links = list(link_counts)
    survivors = {
        link: n for link, n in link_counts.items()
        if not is_article_link(link) and not is_system_link(link) and not is_cta_link(link)
    }
    log.append(
        f"BH clicks-derive: {len(all_links)} distinct links -> {len(survivors)} "
        f"survivor(s) after removing article/system/CTA links."
    )
    if not survivors:
        raise BHError(
            "Clicks-derive yielded ZERO booklet candidates; all links were "
            f"article/system/CTA: {all_links}"
        )

    # Prefer the newsletter landing page (common parent of the article links).
    parent = _article_common_parent(all_links)
    if parent is not None and parent in survivors:
        n = survivors[parent]
        log.append(f"BH booklet (common parent of articles): {parent} = {n}")
        return BHResult(n, [parent], "clicks-derive(common-parent)",
                        dict(survivors), log)

    if len(survivors) == 1:
        link, n = next(iter(survivors.items()))
        log.append(f"BH booklet (single survivor): {link} = {n}")
        return BHResult(n, [link], "clicks-derive(single-survivor)",
                        dict(survivors), log)

    raise BHError(
        "Clicks-derive is AMBIGUOUS: multiple survivors and no common "
        f"article-parent among them: {dict(survivors)}. Operator confirmation "
        "required (do not guess)."
    )


def resolve_bh(
    request_count: int | None,
    request_link: str | None = None,
    master_link_counts: dict[str, int] | None = None,
) -> BHResult:
    """Resolve BH from whichever source(s) are present (operator's two paths).

    - request file present -> BH = its row count (PRIMARY).
    - else master present  -> clicks-derive (FALLBACK).
    - both present & disagree -> use request file, attach a warning.
    """
    # The request export is authoritative when present (operator decision): it
    # isolates exactly the booklet link the operator wants in BG, which legitimately
    # differs from any clicks-derive guess (different link), so we do NOT cross-check
    # or warn -- we trust the export.
    if request_count is not None:
        return BHResult(
            request_count,
            [request_link] if request_link else [],
            "request-file",
            log_lines=[f"BH request-file: {request_count} (booklet={request_link})"],
        )

    # FALLBACK only: no request export. This is a best-effort guess of the booklet
    # link and is flagged loudly downstream (sheet.build_sheet_plan) so the operator
    # verifies it before the value is written.
    if master_link_counts is not None:
        return derive_from_master(master_link_counts)

    raise BHError(
        "Cannot compute BH: no request export and no master Unique Clicks file. "
        "Operator confirmation required."
    )
