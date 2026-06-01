"""Engagement Tracking Reports — downstream processing (Phase 1).

Identify exports by content, parse metric row counts, compute the BH booklet
aggregation, and produce the deterministic finished names + Sheet values.
"""

from . import bh, identify, intake, naming, overview, parse, pipeline, sheet

__all__ = ["bh", "identify", "intake", "naming", "overview", "parse", "pipeline", "sheet"]
__version__ = "0.1.0"
