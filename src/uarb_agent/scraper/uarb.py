"""Playwright driver for https://uarb.novascotia.ca/fmi/webd/UARB15.

Flow (mirrors what a person does):

1. open the portal, type the matter number into "Go Directly to Matter", press Enter;
2. read the matter header (title, type, category, dates, status, outcome) and the per-tab counts;
3. click the requested tab, walk the rows top to bottom, press GO GET IT on each, save the file
   offered by the "Download Files" dialog, close the dialog;
4. stop after ``limit`` files (default 10).

Every portal session is a fresh browser context, so a retry after a FileMaker session problem
starts clean.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ..config import UARB_URL, Settings
from ..models import (
    DocumentRow,
    DocumentType,
    DownloadedFile,
    MatterMetadata,
    MatterNotFoundError,
    MatterRequest,
    PortalError,
    ScrapeResult,
    SkippedDocument,
    TabCounts,
)
from . import selectors as S

log = logging.getLogger(__name__)

# Every piece of header text with its bounding box. Labels ("Matter No", "Type", ...) and
# values are both plain text blocks on the FileMaker layout, so classification happens in
# Python by content (see ``parse_header_boxes``).
HEADER_GEOMETRY_JS = """
() => {
  const grid = document.querySelector('.v-grid');
  const gridTop = grid ? grid.getBoundingClientRect().top : Infinity;
  const items = [];
  const push = (el) => {
    const r = el.getBoundingClientRect();
    const text = (el.innerText || '').replace(/\\u00a0/g, ' ').trim();
    if (!text || r.width === 0 || r.height === 0) return;
    if (el.closest('.v-grid') || el.closest('.v-window') || el.closest('button')) return;
    if (r.top >= gridTop) return;
    items.push({text, x: r.left, y: r.top, w: r.width, h: r.height});
  };
  document.querySelectorAll('.fm-textarea.v-readonly .inner_border > .text').forEach(push);
  document.querySelectorAll('.v-label.text').forEach(push);
  return items;
}
"""

ROW_GEOMETRY_JS = """
(rows) => rows.map((tr, i) => {
  const r = tr.getBoundingClientRect();
  return {i, y: r.top, h: r.height, text: tr.innerText || ''};
})
"""


@dataclass
class _Box:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def lines(self) -> list[str]:
        return [s.strip() for s in self.text.splitlines() if s.strip()]

    @property
    def is_label(self) -> bool:
        lines = self.lines
        return bool(lines) and all(S.normalise_label(s) in S.LABEL_MAP for s in lines)


def parse_header_boxes(boxes: list[dict], matter: str) -> MatterMetadata:
    """Match header values to the label above them using page geometry.

    Labels are the text blocks whose every line is a known field name ("Matter No" over
    "Status", "Type" over "Category", ...). Each value is assigned to the nearest label that
    overlaps it horizontally and sits above it. Pure function so it can be unit tested with
    recorded geometry.
    """
    items = [_Box(**{k: b[k] for k in ("text", "x", "y", "w", "h")}) for b in boxes]
    labels = [b for b in items if b.is_label]
    values = [b for b in items if not b.is_label]
    meta = MatterMetadata(matter=matter)
    used: set[int] = set()

    for label in labels:
        sub_labels = label.lines
        candidates = sorted(
            (
                (idx, v)
                for idx, v in enumerate(values)
                if idx not in used
                and v.y >= label.bottom - 4
                and v.x < label.right + 12
                and v.right > label.x - 12
            ),
            key=lambda iv: (iv[1].y, iv[1].x),
        )
        for sub_label, (idx, value) in zip(sub_labels, candidates, strict=False):
            used.add(idx)
            key = S.normalise_label(sub_label)
            field = S.LABEL_MAP.get(key)
            text = " ".join(value.text.split())
            meta.raw_fields[sub_label] = text
            if field is None:
                continue
            if field.startswith("date_"):
                setattr(meta, field, parse_date(text))
            elif field == "matter":
                continue
            else:
                setattr(meta, field, text)
    return meta


def parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        with suppress(ValueError):
            return datetime.strptime(text, fmt).date()
    return None


def parse_row_text(index: int, text: str) -> DocumentRow | None:
    """Turn a grid row's innerText into a :class:`DocumentRow`.

    Rows read like ``"104160\\n\\nUtility (Board) - Update\\n\\n10/28/2026\\n\\nPublic\\n\\n
    Preview\\nGO GET IT\\n.pdf"`` for documents and ``"Application\\n\\n04/07/2025\\n\\nPublic
    \\n\\nH-1\\n\\nPreview\\nGO GET IT\\n.pdf"`` for exhibits.
    """
    lines = [ln.strip() for ln in text.replace("\u00a0", " ").splitlines()]
    lines = [ln for ln in lines if ln and ln.upper() not in S.ROW_NOISE]
    if not lines:
        return None

    doc_no = ""
    security = ""
    file_type = ""
    row_date: date | None = None
    rest: list[str] = []
    for ln in lines:
        if not row_date and S.DATE_RE.match(ln):
            row_date = parse_date(ln)
        elif not security and S.SECURITY_RE.match(ln):
            security = ln.title()
        elif not file_type and S.FILE_TYPE_RE.match(ln) and ln.startswith("."):
            file_type = ln.lstrip(".").lower()
        elif not doc_no and (S.DOC_NO_RE.match(ln) or S.EXHIBIT_NO_RE.match(ln)):
            doc_no = ln
        else:
            rest.append(ln)
    if not file_type:
        for ln in list(rest):
            if S.FILE_TYPE_RE.match(ln) and len(ln) <= 5 and ln.lower() in {
                "pdf", "doc", "docx", "xls", "xlsx", "zip", "mp3", "mp4", "wav", "txt"}:
                file_type = ln.lower()
                rest.remove(ln)
                break
    title = max(rest, key=len) if rest else ""
    if not doc_no and not title:
        return None
    return DocumentRow(
        index=index,
        doc_no=doc_no or f"row-{index}",
        title=title,
        security=security or "Public",
        file_type=file_type,
        date=row_date,
    )


def parse_tab_counts(texts: list[str]) -> TabCounts:
    counts = TabCounts()
    seen: set[DocumentType] = set()
    for text in texts:
        m = S.TAB_LABEL_RE.match(" ".join(text.split()))
        if not m:
            continue
        doc_type = DocumentType(m.group(1).title())
        if doc_type in seen:
            continue
        seen.add(doc_type)
        counts.set_for_type(doc_type, int(m.group(2)))
    if not seen:
        raise PortalError("no document tabs found on the matter page")
    return counts


class UarbScraper:
    """Fetch metadata and documents for one matter from the live portal."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        headless: bool | None = None,
        channel: str | None = None,
        slow_mo: int = 0,
        viewport: tuple[int, int] = (1400, 2400),
    ) -> None:
        self.settings = settings or Settings()
        self.headless = self.settings.headless if headless is None else headless
        self.channel = channel or self.settings.browser_channel
        self.slow_mo = slow_mo
        self.viewport = viewport
        self.timeout = self.settings.page_timeout_ms
        self.url = self.settings.portal_url or UARB_URL

    # ----------------------------------------------------------------------------- public API
    def fetch(self, request: MatterRequest, out_dir: Path, limit: int = 10) -> ScrapeResult:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                return self._fetch_once(request, out_dir, limit, attempt)
            except MatterNotFoundError:
                raise
            except (PortalError, PlaywrightTimeout, PlaywrightError) as exc:
                last_error = exc
                log.warning("attempt %s failed for %s: %s", attempt, request.matter, exc)
                if attempt == 2:
                    break
                time.sleep(3)
        raise PortalError(f"portal automation failed after 2 attempts: {last_error}")

    # ---------------------------------------------------------------------------- one attempt
    def _fetch_once(
        self, request: MatterRequest, out_dir: Path, limit: int, attempt: int
    ) -> ScrapeResult:
        with sync_playwright() as pw:
            browser = self._launch(pw)
            context = browser.new_context(
                viewport={"width": self.viewport[0], "height": self.viewport[1]},
                accept_downloads=True,
            )
            context.set_default_timeout(self.timeout)
            page = context.new_page()
            try:
                return self._run(page, request, out_dir, limit)
            except MatterNotFoundError:
                raise
            except Exception:
                self._failure_screenshot(page, out_dir, request.matter, attempt)
                raise
            finally:
                self._close_quietly(context, browser)

    def _launch(self, pw: Playwright) -> Browser:
        kwargs: dict = {"headless": self.headless, "slow_mo": self.slow_mo}
        if self.channel and self.channel != "chromium":
            kwargs["channel"] = self.channel
        return pw.chromium.launch(**kwargs)

    @staticmethod
    def _close_quietly(context: BrowserContext, browser: Browser) -> None:
        with suppress(Exception):
            context.close()
        with suppress(Exception):
            browser.close()

    @staticmethod
    def _failure_screenshot(page: Page, out_dir: Path, matter: str, attempt: int) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"failure-{matter}-attempt{attempt}-{stamp}.png"
        with suppress(Exception):
            page.screenshot(path=str(path), full_page=False)
            log.info("saved failure screenshot to %s", path)

    def _run(self, page: Page, request: MatterRequest, out_dir: Path, limit: int) -> ScrapeResult:
        self._open_landing(page)
        self._search_matter(page, request.matter)
        metadata = self._read_metadata(page, request.matter)
        counts = self._read_tab_counts(page)
        result = ScrapeResult(request=request, metadata=metadata, counts=counts)
        log.info("%s: %s | counts=%s", request.matter, metadata.title, counts)

        expected = counts.for_type(request.document_type)
        if expected == 0:
            log.info("%s has no %s; nothing to download", request.matter, request.document_type)
            return result

        self._open_tab(page, request.document_type)
        self._download_rows(page, result, out_dir, limit)
        return result

    # ------------------------------------------------------------------------------ navigation
    def _open_landing(self, page: Page) -> None:
        page.goto(self.url, wait_until="domcontentloaded")
        page.wait_for_selector(S.LANDING_MARKER)
        page.wait_for_timeout(1000)
        self._raise_on_session_problem(page)

    def _search_matter(self, page: Page, matter: str) -> None:
        placeholder = page.locator("div.placeholder", has_text=S.MATTER_PLACEHOLDER).first
        box = placeholder.locator("xpath=..").locator("div.text").first
        for attempt in range(3):
            box.click()
            page.wait_for_selector(S.MATTER_INPUT_ACTIVE, timeout=10_000)
            page.keyboard.type(matter, delay=40)
            page.wait_for_timeout(400)
            if box.inner_text().strip().upper() == matter.upper():
                break
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            if attempt == 2:
                raise PortalError("could not type the matter number into the search box")
        page.keyboard.press("Enter")

        tabs = page.locator(S.TAB_SPAN, has_text=S.TAB_LABEL_RE)
        dialog = page.locator(S.DIALOG)
        tabs.or_(dialog).first.wait_for(state="visible")
        if dialog.count():
            title = self._dialog_title(dialog.first)
            body = dialog.first.inner_text()
            if any(t.lower() in (title + body).lower() for t in S.NOT_FOUND_TITLES):
                self._click_dialog_button(dialog.first, "OK")
                raise MatterNotFoundError(f"the portal has no matter {matter}")
            raise PortalError(f"unexpected dialog after search: {title}: {body[:200]}")
        page.wait_for_timeout(800)

    def _read_metadata(self, page: Page, matter: str) -> MatterMetadata:
        exact_matter = re.compile(rf"^\s*{matter}\s*$", re.I)
        page.locator(S.READONLY_VALUE, has_text=exact_matter).first.wait_for(state="visible")
        boxes = page.evaluate(HEADER_GEOMETRY_JS)
        meta = parse_header_boxes(boxes, matter)
        if not meta.title:
            # Fall back to the bold title block even if label matching failed.
            title_box = page.locator(".v-label.text").first
            if title_box.count():
                meta.title = " ".join(title_box.inner_text().split())
        if not meta.title:
            raise PortalError("could not read the matter title from the header")
        return meta

    def _read_tab_counts(self, page: Page) -> TabCounts:
        spans = page.locator(S.TAB_SPAN, has_text=S.TAB_LABEL_RE)
        texts = [spans.nth(i).inner_text() for i in range(spans.count())]
        return parse_tab_counts(texts)

    def _open_tab(self, page: Page, doc_type: DocumentType) -> None:
        pattern = re.compile(rf"^{re.escape(doc_type.value)}\s*-\s*\d+$", re.I)
        tab = page.locator(S.TAB_SPAN, has_text=pattern).first
        tab.click()
        selected = page.locator(S.TAB_SEGMENT_SELECTED, has_text=pattern)
        dialog = page.locator(S.DIALOG)
        selected.or_(dialog).first.wait_for(state="visible")
        if dialog.count():
            title = self._dialog_title(dialog.first)
            if any(t.lower() in title.lower() for t in S.EMPTY_TAB_TITLES):
                self._click_dialog_button(dialog.first, "OK")
                raise PortalError(f"tab {doc_type} reported no records despite a non-zero count")
            raise PortalError(f"unexpected dialog when opening {doc_type}: {title}")
        page.wait_for_selector(S.GRID_ROW)
        self._wait_for_rows_to_settle(page)

    def _wait_for_rows_to_settle(self, page: Page, quiet_ms: int = 700, max_ms: int = 8000) -> None:
        deadline = time.monotonic() + max_ms / 1000
        previous = None
        while time.monotonic() < deadline:
            snapshot = [r["text"] for r in self._row_geometry(page)]
            if snapshot and snapshot == previous:
                return
            previous = snapshot
            page.wait_for_timeout(quiet_ms)

    # ------------------------------------------------------------------------------------ rows
    def _row_geometry(self, page: Page) -> list[dict]:
        rows = page.locator(S.GRID_ROW)
        if rows.count() == 0:
            return []
        return rows.evaluate_all(ROW_GEOMETRY_JS)

    def _visible_rows(self, page: Page) -> list[tuple[DocumentRow, Locator]]:
        """Rendered rows in visual (top to bottom) order with their locators."""
        rows = page.locator(S.GRID_ROW)
        geometry = self._row_geometry(page)
        ordered = sorted(geometry, key=lambda g: g["y"])
        out: list[tuple[DocumentRow, Locator]] = []
        for position, g in enumerate(ordered, start=1):
            parsed = parse_row_text(position, g["text"])
            if parsed is None:
                continue
            out.append((parsed, rows.nth(g["i"])))
        return out

    @staticmethod
    def _row_key(row: DocumentRow) -> tuple[str, str, str]:
        return (row.doc_no, row.title, row.date.isoformat() if row.date else "")

    def _scroll_grid(self, page: Page) -> bool:
        """Scroll the virtualized grid down one screen. Returns False when at the bottom."""
        return bool(
            page.evaluate(
                """(selector) => {
                  const s = document.querySelector(selector);
                  if (!s) return false;
                  const before = s.scrollTop;
                  s.scrollTop = before + Math.max(200, s.clientHeight * 0.8);
                  return s.scrollTop > before;
                }""",
                S.GRID_VSCROLLER,
            )
        )

    def _download_rows(
        self, page: Page, result: ScrapeResult, out_dir: Path, limit: int
    ) -> None:
        processed: set[tuple[str, str, str]] = set()
        listed: dict[tuple[str, str, str], DocumentRow] = {}
        taken_names: set[str] = set()
        stalls = 0

        while len(result.downloaded) < limit:
            rows = self._visible_rows(page)
            for row, _loc in rows:
                listed.setdefault(self._row_key(row), row)
            pending = [(r, loc) for r, loc in rows if self._row_key(r) not in processed]
            if not pending:
                if not self._scroll_grid(page):
                    break
                page.wait_for_timeout(600)
                self._wait_for_rows_to_settle(page, quiet_ms=400, max_ms=4000)
                stalls += 1
                if stalls > 40:
                    break
                continue

            row, locator = pending[0]
            processed.add(self._row_key(row))
            row.index = len(processed)
            if row.is_confidential:
                result.skipped.append(SkippedDocument(row, "confidential"))
                log.info("skipping confidential row %s (%s)", row.doc_no, row.title)
                continue
            try:
                files = self._download_row(page, locator, row, out_dir, taken_names)
            except (PlaywrightTimeout, PortalError) as exc:
                self._dismiss_dialogs(page)
                result.skipped.append(SkippedDocument(row, f"download failed: {exc}"))
                log.warning("download failed for %s: %s", row.doc_no, exc)
                continue
            if not files:
                result.skipped.append(SkippedDocument(row, "no file offered by the portal"))
                continue
            for f in files:
                if len(result.downloaded) < limit:
                    result.downloaded.append(f)
            log.info("downloaded %s -> %s", row.doc_no, ", ".join(f.filename for f in files))

        result.listed_rows = list(listed.values())

    def _download_row(
        self,
        page: Page,
        row_locator: Locator,
        row: DocumentRow,
        out_dir: Path,
        taken_names: set[str],
    ) -> list[DownloadedFile]:
        button = row_locator.locator(S.GO_GET_IT_BUTTON).first
        button.scroll_into_view_if_needed()
        button.click()
        dialog = page.locator(S.DIALOG, has_text=S.DOWNLOAD_DIALOG_TITLE).first
        dialog.wait_for(state="visible", timeout=20_000)
        page.wait_for_timeout(300)

        saved: list[DownloadedFile] = []
        file_buttons = dialog.locator(S.DOWNLOAD_FILE_BUTTON)
        for i in range(file_buttons.count()):
            file_button = file_buttons.nth(i)
            caption = file_button.inner_text().strip()
            with page.expect_download(timeout=self.timeout) as dl_info:
                file_button.click()
            download = dl_info.value
            name = self._unique_name(download.suggested_filename or caption or f"{row.doc_no}.bin",
                                     row.doc_no, taken_names)
            target = out_dir / name
            download.save_as(str(target))
            size = target.stat().st_size
            if size == 0:
                target.unlink(missing_ok=True)
                raise PortalError(f"empty download for {row.doc_no}")
            saved.append(DownloadedFile(row=row, path=target, size_bytes=size))

        self._click_dialog_button(dialog, "Close")
        with suppress(PlaywrightTimeout):
            dialog.wait_for(state="hidden", timeout=10_000)
        return saved

    @staticmethod
    def _unique_name(suggested: str, doc_no: str, taken: set[str]) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", suggested).strip("._") or "file"
        candidate = safe
        if candidate in taken:
            stem, dot, ext = safe.rpartition(".")
            candidate = f"{doc_no}_{safe}" if not dot else f"{stem}_{doc_no}.{ext}"
            n = 2
            while candidate in taken:
                candidate = f"{stem or safe}_{doc_no}_{n}{'.' + ext if dot else ''}"
                n += 1
        taken.add(candidate)
        return candidate

    # --------------------------------------------------------------------------------- dialogs
    @staticmethod
    def _dialog_title(dialog: Locator) -> str:
        header = dialog.locator(S.DIALOG_HEADER)
        if header.count():
            return header.first.inner_text().strip()
        return dialog.inner_text().strip().splitlines()[0] if dialog.inner_text().strip() else ""

    @staticmethod
    def _click_dialog_button(dialog: Locator, label: str) -> None:
        button = dialog.locator(S.DIALOG_BUTTON, has_text=re.compile(rf"^\s*{label}\s*$", re.I))
        if button.count():
            button.first.click()

    def _dismiss_dialogs(self, page: Page) -> None:
        dialogs = page.locator(S.DIALOG)
        for i in range(dialogs.count()):
            d = dialogs.nth(i)
            for label in ("Close", "OK", "Cancel"):
                with suppress(Exception):
                    self._click_dialog_button(d, label)
        page.wait_for_timeout(300)

    @staticmethod
    def _raise_on_session_problem(page: Page) -> None:
        text = page.inner_text("body")
        for marker in S.SESSION_PROBLEM_MARKERS:
            if marker.lower() in text.lower():
                raise PortalError(f"portal reported: {marker}")
