"""Phase 3: Apply redactions to PDF."""

import logging
from pathlib import Path
from typing import Dict, List, Tuple

import fitz
import pdfplumber

from blackletter.config import RedactionConfig
from blackletter.utils.text import redact_text_lines_in_window

logger = logging.getLogger(__name__)


class TextRedactor:
    """Handles text-level redactions within windows."""

    def __init__(self, config: RedactionConfig):
        self.config = config

    def redact_text_window(
            self, page_pl, page_fitz, win_pdf: Tuple[float, float, float, float]
    ):
        """Redact text lines within a window using pdfplumber."""
        redact_text_lines_in_window(
            page_pl=page_pl,
            page_fitz=page_fitz,
            win_pdf=win_pdf,
            pad=self.config.text_pad,
            y_tol=self.config.y_tolerance,
            merge_gap=self.config.merge_gap,
        )


class PDFRedactor:
    """Applies all redactions to PDF in Phase 3."""

    def __init__(self, config: RedactionConfig):
        self.config = config
        self.text_redactor = TextRedactor(config)

    def redact(
            self,
            pdf_path: Path,
            redaction_instructions: List[Dict],
            global_objects: List[Dict],
            page_dimensions: Dict,
            page_columns_px: Dict,
            output_folder: Path,
    ) -> Path:
        """Apply all redactions to PDF.

        Args:
            pdf_path: Input PDF path
            redaction_instructions: List of start->end redaction pairs
            global_objects: All detected objects
            page_dimensions: Map of page_idx to (pdf_w, pdf_h, img_w, img_h)
            page_columns_px: Map of page_idx to column boundaries
            output_folder: Where to save redacted PDF

        Returns:
            Path to redacted PDF
        """
        logger.info("Starting PHASE 3: Applying redactions")

        doc = fitz.open(pdf_path)

        with pdfplumber.open(pdf_path) as pdf_read:
            for page_idx in range(len(doc)):
                page_fitz = doc[page_idx]
                page_pl = pdf_read.pages[page_idx]

                if page_idx not in page_dimensions:
                    continue

                pdf_w, pdf_h, img_w, img_h = page_dimensions[page_idx]
                scale_x = pdf_w / img_w
                scale_y = pdf_h / img_h

                objs_on_page = [
                    o for o in global_objects if o["page_index"] == page_idx
                ]

                self._apply_body_redactions(
                    page_fitz,
                    page_pl,
                    redaction_instructions,
                    page_idx,
                    objs_on_page,
                    img_h,
                    page_columns_px,
                    scale_x,
                    scale_y,
                )

                self._apply_object_redactions(
                    page_fitz, page_pl, objs_on_page, scale_x, scale_y
                )

                page_fitz.apply_redactions()

        output_path = output_folder / f"{pdf_path.stem}_redacted.pdf"
        output_folder.mkdir(exist_ok=True, parents=True)
        doc.save(output_path, garbage=4, deflate=True)
        doc.close()

        logger.info(f"Saved redacted PDF to: {output_path}")
        return output_path

    def _apply_instruction(
            self,
            page_fitz,
            page_pl,
            instr: Dict,
            page_idx: int,
            ceiling_y: int,
            limit_bottom_left: int,
            limit_bottom_right: int,
            LEFT_X1: int,
            LEFT_X2: int,
            RIGHT_X1: int,
            RIGHT_X2: int,
            scale_x: float,
            scale_y: float,
    ):
        """Apply a single redaction instruction."""
        start = instr["start"]
        end = instr["end"]

        if end["page_index"] < page_idx or start["page_index"] > page_idx:
            return

        def do_column_box(y_top_px: int, y_bottom_px: int, is_left: bool):
            """Redact a column from y_top to y_bottom."""
            y_top_px = int(max(ceiling_y, y_top_px))
            max_y_px = limit_bottom_left if is_left else limit_bottom_right
            y_bottom_px = int(min(max_y_px, y_bottom_px))

            if y_bottom_px <= y_top_px:
                return

            xx1_px = LEFT_X1 if is_left else RIGHT_X1
            xx2_px = LEFT_X2 if is_left else RIGHT_X2

            x0_pdf = xx1_px * scale_x
            x1_pdf = xx2_px * scale_x
            y0_pdf = y_top_px * scale_y
            y1_pdf = y_bottom_px * scale_y

            self.text_redactor.redact_text_window(
                page_pl=page_pl,
                page_fitz=page_fitz,
                win_pdf=(x0_pdf, y0_pdf, x1_pdf, y1_pdf),
            )

        s_col = start["col"] == "LEFT"
        e_col = end["col"] == "LEFT"

        # Case 1: Start & End on same page
        if start["page_index"] == page_idx and end["page_index"] == page_idx:
            sy = start["y2"] + self.config.start_offset
            ey = end["y1"] + self.config.end_offset
            if s_col == e_col:
                do_column_box(sy, ey, s_col)
            else:
                do_column_box(sy, 9999, True)
                do_column_box(0, ey, False)

        # Case 2: Start on previous page, end on this page
        elif start["page_index"] < page_idx and end["page_index"] == page_idx:
            ey = end["y1"] + self.config.end_offset
            if e_col:
                do_column_box(0, ey, True)
            else:
                do_column_box(0, 9999, True)
                do_column_box(0, ey, False)

        # Case 3: Start on this page, end on future page
        elif start["page_index"] == page_idx and end["page_index"] > page_idx:
            sy = start["y2"] + self.config.start_offset
            if s_col:
                do_column_box(sy, 9999, True)
                do_column_box(0, 9999, False)
            else:
                do_column_box(sy, 9999, False)

        # Case 4: Middle page (between start and end)
        elif start["page_index"] < page_idx and end["page_index"] > page_idx:
            do_column_box(0, 9999, True)
            do_column_box(0, 9999, False)

    def _apply_body_redactions(
            self,
            page_fitz,
            page_pl,
            instructions: List[Dict],
            page_idx: int,
            objs_on_page: List[Dict],
            img_h: int,
            page_columns_px: Dict,
            scale_x: float,
            scale_y: float,
    ):
        """Apply body text redactions for a page."""
        DEFAULT_BOTTOM = img_h - 60
        limit_bottom_left = DEFAULT_BOTTOM
        limit_bottom_right = DEFAULT_BOTTOM

        # Find footnote boundaries
        for o in objs_on_page:
            if o["label"] == "footnotes":
                y1 = o["y1"]
                if o["col"] == "LEFT":
                    limit_bottom_left = min(limit_bottom_left, y1)
                elif o["col"] == "RIGHT":
                    limit_bottom_right = min(limit_bottom_right, y1)
                else:
                    limit_bottom_left = min(limit_bottom_left, y1)
                    limit_bottom_right = min(limit_bottom_right, y1)

        # Find header ceiling
        ceiling_y = 90
        headers = [o for o in objs_on_page if o["label"] == "header"]
        if headers:
            max_header_bottom = max([h["y2"] for h in headers])
            if max_header_bottom < (img_h / 3):
                ceiling_y = max(ceiling_y, max_header_bottom + 5)

        # Get column boundaries for this page
        LEFT_X1, LEFT_X2, RIGHT_X1, RIGHT_X2, split_x = page_columns_px.get(
            page_idx,
            (
                30,
                int(img_h / 2 - 5),
                int(img_h / 2 + 5),
                int(img_h - 30),
                int(img_h / 2),
            ),
        )

        # Apply each instruction
        for instr in instructions:
            self._apply_instruction(
                page_fitz,
                page_pl,
                instr,
                page_idx,
                ceiling_y,
                limit_bottom_left,
                limit_bottom_right,
                LEFT_X1,
                LEFT_X2,
                RIGHT_X1,
                RIGHT_X2,
                scale_x,
                scale_y,
            )

    def _apply_object_redactions(
            self,
            page_fitz,
            page_pl,
            objs_on_page: List[Dict],
            scale_x: float,
            scale_y: float,
    ):
        """Apply redactions for specific detected objects."""
        from blackletter.utils.header import HeaderProcessor

        header_coord = None

        # Redact specific object types
        for o in objs_on_page:
            label = o["label"]

            if label in ["line", "Key", "brackets", "order"]:
                c = [int(x) for x in o["coords"]]
                self._add_redaction_box(
                    page_fitz, c[0], c[1], c[2], c[3], scale_x, scale_y
                )

            if label == "header":
                header_coord = [int(x) for x in o["coords"]]

        # Header redaction with special processing
        hdr = HeaderProcessor.redaction_bbox_for_header(
            page_pl,
            top_pts=self.config.header_top_pts,
            gap_pts=self.config.header_gap_pts,
            y_tol=self.config.header_y_tol,
            margin_pts=self.config.header_margin_pts,
            pad_x=self.config.header_pad_x,
            pad_y=self.config.header_pad_y,
        )

        if hdr is not None:
            x0, y0, x1, y1 = hdr
            ry2 = header_coord[3] * scale_y if header_coord else y1
            page_fitz.add_redact_annot(
                fitz.Rect(x0, y0, x1, max(y1, ry2)),
                fill=self.config.redaction_fill
            )
        elif header_coord:
            self._add_redaction_box(
                page_fitz,
                header_coord[0],
                header_coord[1],
                header_coord[2],
                header_coord[3],
                scale_x,
                scale_y,
            )

    @staticmethod
    def _add_redaction_box(
            page_fitz, x1: int, y1: int, x2: int, y2: int, scale_x: float,
            scale_y: float
    ):
        """Add a redaction box to a page."""
        if y2 <= y1 or x2 <= x1:
            return

        rx1, ry1 = x1 * scale_x, y1 * scale_y
        rx2, ry2 = x2 * scale_x, y2 * scale_y

        page_fitz.add_redact_annot(fitz.Rect(rx1, ry1, rx2, ry2),
                                   fill=(0.2, 0.2, 0.2))