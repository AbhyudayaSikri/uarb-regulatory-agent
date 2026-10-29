"""Selectors and text patterns for the UARB portal (FileMaker WebDirect on Vaadin 8).

Everything here was verified against the live site on 2026-10-28. The portal renders no stable
ids (they look like ``b0p1o299i3i0r1`` and encode layout object + row index), so selectors lean
on structural classes and visible text instead.
"""

from __future__ import annotations

import re

# Landing page -------------------------------------------------------------------------------
LANDING_MARKER = "text=Go Directly to Matter"
MATTER_PLACEHOLDER = re.compile(r"eg\s*M01234", re.I)
# The editable box is a div that becomes contenteditable once clicked.
MATTER_INPUT_ACTIVE = 'div.inner_border > div.text[contenteditable="true"]'

# Matter page ---------------------------------------------------------------------------------
TAB_LABEL_RE = re.compile(
    r"^(Exhibits|Key Documents|Other Documents|Transcripts|Recordings)\s*-\s*(\d+)$", re.I
)
TAB_SPAN = "span.fm-text-character"
TAB_SEGMENT = ".iwps_button_bar_segment"
TAB_SEGMENT_SELECTED = ".iwps_button_bar_segment.fm-selected"
READONLY_VALUE = ".fm-textarea.v-readonly .inner_border > .text"

# Document list --------------------------------------------------------------------------------
GRID = ".v-grid"
GRID_ROW = "tr.v-grid-row-has-data"
GRID_VSCROLLER = ".v-grid-scroller-vertical"
GO_GET_IT_BUTTON = "button:has-text('GO GET IT')"
ROW_NOISE = {"GO GET IT", "PREVIEW"}
DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
SECURITY_RE = re.compile(r"^(public|confidential|restricted|sealed|protected)$", re.I)
FILE_TYPE_RE = re.compile(r"^\.?[a-z0-9]{1,5}$", re.I)
DOC_NO_RE = re.compile(r"^\d{3,8}$")
EXHIBIT_NO_RE = re.compile(r"^[A-Z]{1,4}-\d+[A-Za-z0-9().\-/ ]*$")

# Dialogs --------------------------------------------------------------------------------------
DIALOG = ".v-window"
DIALOG_HEADER = ".v-window-header"
DIALOG_BUTTON = ".v-button"
DOWNLOAD_DIALOG_TITLE = "Download Files"
DOWNLOAD_FILE_BUTTON = ".v-button.fm-download-button"
NOT_FOUND_TITLES = ("No Records Found",)
EMPTY_TAB_TITLES = ("No Matching Records",)
SESSION_PROBLEM_MARKERS = ("Communication problem", "Session Expired", "Authentication problem")

# Header label -> metadata field. Lower-cased, punctuation-insensitive match.
LABEL_MAP = {
    "matter no": "matter",
    "matter number": "matter",
    "status": "status",
    "title description": "title",
    "title": "title",
    "type": "type",
    "category": "category",
    "date received": "date_received",
    "decision date": "date_final_submissions",
    "date final submissions": "date_final_submissions",
    "final submissions": "date_final_submissions",
    "outcome": "outcome",
}


def normalise_label(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())
