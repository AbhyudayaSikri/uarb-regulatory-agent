"""An in-memory scraper used by the test-suite and by ``--fake`` dry runs."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..models import (
    DocumentRow,
    DocumentType,
    DownloadedFile,
    MatterMetadata,
    MatterNotFoundError,
    MatterRequest,
    ScrapeResult,
    SkippedDocument,
    TabCounts,
)

M12205 = MatterMetadata(
    matter="M12205",
    title=(
        "Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project"
        " - $69,275,000"
    ),
    status="Closed",
    type="Water",
    category="Capital Expenditure Approvals",
    date_received=date(2025, 4, 7),
    date_final_submissions=date(2025, 10, 23),
    outcome="Directions given",
)
M12205_COUNTS = TabCounts(exhibits=13, key_documents=6, other_documents=44)


class FakeScraper:
    """Produces deterministic results and writes small placeholder PDFs to ``out_dir``."""

    def __init__(
        self,
        matters: dict[str, tuple[MatterMetadata, TabCounts]] | None = None,
        *,
        confidential_every: int = 0,
        file_size: int = 2048,
    ) -> None:
        self.matters = matters or {"M12205": (M12205, M12205_COUNTS)}
        self.confidential_every = confidential_every
        self.file_size = file_size
        self.calls: list[tuple[MatterRequest, int]] = []

    def fetch(self, request: MatterRequest, out_dir: Path, limit: int) -> ScrapeResult:
        self.calls.append((request, limit))
        if request.matter not in self.matters:
            raise MatterNotFoundError(f"the portal has no matter {request.matter}")
        metadata, counts = self.matters[request.matter]
        result = ScrapeResult(request=request, metadata=metadata, counts=counts)
        available = counts.for_type(request.document_type)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, available + 1):
            if len(result.downloaded) >= limit:
                break
            confidential = self.confidential_every and i % self.confidential_every == 0
            is_exhibit = request.document_type is DocumentType.EXHIBITS
            row = DocumentRow(
                index=i,
                doc_no=f"H-{i}" if is_exhibit else str(100000 + i),
                title=f"{request.document_type.singular} {i}",
                security="Confidential" if confidential else "Public",
                file_type="pdf",
                date=date(2025, 4, min(i, 28)),
            )
            result.listed_rows.append(row)
            if confidential:
                result.skipped.append(SkippedDocument(row, "confidential"))
                continue
            path = out_dir / f"{row.doc_no}.pdf"
            path.write_bytes(b"%PDF-1.4\n" + b"0" * (self.file_size - 9))
            result.downloaded.append(DownloadedFile(row=row, path=path, size_bytes=self.file_size))
        return result
