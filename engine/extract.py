"""Extract plain text from an uploaded CV file.

Application/matching CVs are stored as text (they feed scoring + drafting). Users
upload them as .md/.txt, but also commonly as .pdf or .docx — so we pull the text
out server-side rather than forcing a manual conversion. Layout is intentionally
discarded; the human-formatted master lives in the reference PDF.
"""
from __future__ import annotations

import io


class ExtractError(Exception):
    """Raised when a file's text could not be extracted (bad/empty/unsupported)."""


def extract_text(data: bytes, filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return _from_pdf(data)
    if name.endswith(".docx"):
        return _from_docx(data)
    if name.endswith(".doc"):
        raise ExtractError("Legacy .doc isn't supported — save as .docx, .pdf, or .md.")
    # .md / .txt / .markdown / unknown -> treat as UTF-8 text
    return data.decode("utf-8", errors="replace").strip()


def _from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ExtractError("PDF support needs the 'pypdf' package (pip install pypdf).")
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        raise ExtractError(f"Could not read PDF: {type(e).__name__}.")
    text = text.strip()
    if not text:
        raise ExtractError("That PDF has no extractable text (likely a scan/image). "
                           "Export a text PDF, or paste the text instead.")
    return text


def _from_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError:
        raise ExtractError("DOCX support needs the 'python-docx' package.")
    try:
        doc = docx.Document(io.BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        raise ExtractError(f"Could not read DOCX: {type(e).__name__}.")
    text = text.strip()
    if not text:
        raise ExtractError("That DOCX appears to be empty.")
    return text
