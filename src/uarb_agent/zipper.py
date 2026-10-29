"""Bundle downloaded documents into one ZIP that fits under the mail provider's limit."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from .models import DownloadedFile, ScrapeResult, ZipResult


def zip_name(result: ScrapeResult) -> str:
    return f"{result.request.matter}-{result.request.document_type.slug}.zip"


def build_zip(result: ScrapeResult, out_dir: Path, max_bytes: int) -> ZipResult:
    """Write ``<matter>-<type>.zip`` containing the downloaded files plus ``MANIFEST.csv``.

    If the archive would exceed ``max_bytes`` the largest files are dropped one at a time
    until it fits; dropped files are reported so the email can say so.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / zip_name(result)
    included = list(result.downloaded)
    dropped: list[DownloadedFile] = []

    while True:
        size = _write(target, result, included)
        if size <= max_bytes or not included:
            break
        biggest = max(included, key=lambda f: f.size_bytes)
        included.remove(biggest)
        dropped.append(biggest)

    return ZipResult(path=target, size_bytes=size, included=included, dropped_for_size=dropped)


def _write(target: Path, result: ScrapeResult, files: list[DownloadedFile]) -> int:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in files:
            zf.write(f.path, arcname=f.filename)
        zf.writestr("MANIFEST.csv", manifest_csv(result, files))
    return target.stat().st_size


def manifest_csv(result: ScrapeResult, files: list[DownloadedFile]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["matter", "document_type", "order", "doc_no", "title", "date", "security", "file",
         "size_bytes"]
    )
    for f in files:
        writer.writerow(
            [
                result.request.matter,
                result.request.document_type.value,
                f.row.index,
                f.row.doc_no,
                f.row.title,
                f.row.date.isoformat() if f.row.date else "",
                f.row.security,
                f.filename,
                f.size_bytes,
            ]
        )
    return buf.getvalue()
