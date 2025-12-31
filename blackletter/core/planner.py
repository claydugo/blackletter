"""Phase 2: Planning redactions using state machine."""

import logging
from enum import Enum
from typing import Dict, List, Tuple

from blackletter.config import RedactionConfig
from blackletter.utils.filtering import BoxFilter

logger = logging.getLogger(__name__)


class OpinionState(Enum):
    """State machine for opinion detection."""

    WAIT_CAPTION = "WAIT_CAPTION"
    TRACKING = "TRACKING"
    LOCKED_UNTIL_KEY = "LOCKED_UNTIL_KEY"


class OpinionPlanner:
    """Plans redaction instructions using a state machine."""

    def __init__(self, config: RedactionConfig):
        self.config = config
        self.opinion_idx = 0

    def plan(
            self, global_objects: List[Dict], first_page: int
    ) -> Tuple[List[Dict], List[Dict], Dict, Dict]:
        """Plan redaction instructions and identify opinion spans.

        Returns:
            - redaction_instructions: List of start->end redaction pairs
            - opinion_spans: List of detected opinions with metadata
            - page_headers: Dict mapping page_idx to header y-coordinate
            - page_footers: Dict mapping page_idx to footer y-coordinate
        """
        logger.info("Starting PHASE 2: Planning redactions")

        global_objects = BoxFilter.filter_overlapping_boxes(
            global_objects, overlap_threshold=0.6
        )

        # CRITICAL: Sort objects by page and position for proper state machine processing
        global_objects = sorted(
            global_objects, key=lambda o: (o["page_index"], o["col"], o["y1"])
        )

        redaction_instructions = []
        opinion_spans = []
        page_headers = {}
        page_footers = {}

        # State machine variables
        active_start_node = None
        candidate_end_node = None
        opinion_start_caption = None
        current_state = OpinionState.WAIT_CAPTION

        # Collect headers and footers
        for obj in global_objects:
            pg = obj["page_index"]
            label = obj["label"]

            if label == "header":
                page_headers[pg] = obj["y2"]
            elif label == "footnotes":
                y1 = obj["y1"]
                page_footers[pg] = min(page_footers.get(pg, float("inf")), y1)

        # State machine - process objects in order
        for obj in global_objects:
            label = obj["label"]

            if label not in ["caption", "line", "headmatter", "Key"]:
                continue

            # LOCKED: waiting for Key to end the opinion
            if current_state == OpinionState.LOCKED_UNTIL_KEY:
                if label == "Key":
                    self._record_opinion_span(
                        opinion_spans, opinion_start_caption, obj,
                        "caption->Key"
                    )
                    opinion_start_caption = None
                    current_state = OpinionState.WAIT_CAPTION
                continue

            # WAITING: looking for a caption to start
            if current_state == OpinionState.WAIT_CAPTION:
                if label == "caption":
                    active_start_node = obj
                    opinion_start_caption = obj
                    current_state = OpinionState.TRACKING
                continue

            # TRACKING: looking for line/headmatter or next caption/Key
            if current_state == OpinionState.TRACKING:
                if label == "line":
                    redaction_instructions.append(
                        {"start": active_start_node, "end": obj}
                    )
                    current_state = OpinionState.LOCKED_UNTIL_KEY

                elif label == "headmatter":
                    if candidate_end_node is None:
                        candidate_end_node = obj

                elif label == "Key":
                    self._record_opinion_span(
                        opinion_spans, opinion_start_caption, obj,
                        "caption->Key"
                    )

                    if candidate_end_node:
                        redaction_instructions.append(
                            {"start": active_start_node,
                             "end": candidate_end_node}
                        )

                    current_state = OpinionState.WAIT_CAPTION
                    active_start_node = None
                    candidate_end_node = None
                    opinion_start_caption = None

        self._assign_case_names(opinion_spans, first_page)
        logger.info(
            f"Planned {len(redaction_instructions)} redactions, {len(opinion_spans)} opinions"
        )

        return redaction_instructions, opinion_spans, page_headers, page_footers

    def _record_opinion_span(self, spans: List, start: Dict, end: Dict,
                             reason: str):
        """Record an opinion span."""
        if not start or not end:
            return

        self.opinion_idx += 1
        spans.append(
            {"n": self.opinion_idx, "start": start, "end": end,
             "reason": reason}
        )

        sp = start["page_index"] + 1
        ep = end["page_index"] + 1
        logger.info(
            f"Opinion {self.opinion_idx:03d}: pages {sp}–{ep} ({reason})")

    @staticmethod
    def _assign_case_names(opinion_spans: List[Dict], page_start: int):
        """Assign case names to opinions."""
        if not opinion_spans:
            return

        COL_ORDER = {"LEFT": 0, "RIGHT": 1}

        def sort_key(span):
            start = span.get("start", {})
            page = start.get("page_index", 10 ** 9)
            col = COL_ORDER.get(start.get("col"), 99)
            y1 = start.get("y1", 10 ** 9)
            return (page, col, y1)

        opinion_spans.sort(key=sort_key)

        page_counter = {}
        for sp in opinion_spans:
            first_page = sp["start"]["page_index"] + page_start
            counter = page_counter.get(first_page, 0) + 1
            page_counter[first_page] = counter
            sp["case_name"] = f"{first_page:04d}-{counter:02d}"