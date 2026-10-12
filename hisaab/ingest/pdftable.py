"""PDF statements are not supported yet (a best-effort reader would need poppler's `pdftotext`)."""
from __future__ import annotations

from .readers import ReadError


def pdf_to_grid(data: bytes) -> list[list]:
    raise ReadError("PDF statements are not supported yet. Please download the CSV / Excel version of your statement "
                    "from your bank's net-banking site (most banks offer it).")
