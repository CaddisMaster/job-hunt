"""Resumes (#23): what an upload must be, how its text is read, and who may see it.

⚠️ Every query here takes the current user and filters on it (non-negotiable 1).
Another user's resume id is a 404, never a 403: not even its existence leaks.

An upload is someone else's file, so everything about it is distrusted:
- **Its type comes from its content**, never its name or Content-Type. A PDF
  starts with `%PDF-`. A DOCX is a zip holding `word/document.xml`.
- **Its size is capped while reading** (MAX_BYTES), not after.
- **Parsing is bounded.** A DOCX's zip is checked for entry count and unpacked
  size before python-docx opens it (a zip bomb stops there). A PDF's text is read
  from at most MAX_PAGES pages and kept to MAX_TEXT characters.
- **Its filename is only ever displayed** (and sent back on download), never used
  as a path. It's reduced to a plain name first.
"""

import hashlib
import io
import logging
import unicodedata
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from app.models import Resume, User

log = logging.getLogger("apply.resumes")

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 30
MAX_TEXT = 100_000
_MAX_ZIP_ENTRIES = 2_000
_MAX_ZIP_UNPACKED = 50 * 1024 * 1024

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class Rejected(Exception):
    """An upload that isn't stored. The message is shown to the user as it is."""


def sniff(data: bytes) -> str | None:
    """"pdf", "docx", or None, from the bytes alone."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            return None
        if "word/document.xml" in names and "[Content_Types].xml" in names:
            return "docx"
    return None


def clean_filename(name: str | None, kind: str) -> str:
    """A displayable name: no directories, no control characters, at most 255 long."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    return (name or f"resume.{kind}")[:255]


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:MAX_PAGES]:
        parts.append(page.extract_text() or "")
        if sum(len(p) for p in parts) > MAX_TEXT:
            break
    return "\n".join(parts)


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > _MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > _MAX_ZIP_UNPACKED:
            raise Rejected("That DOCX unpacks to far more than a resume does, so it wasn't stored.")
    import docx

    document = docx.Document(io.BytesIO(data))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def extract_text(kind: str, data: bytes) -> str:
    """The file's text, trimmed. Empty means it opened but has no text (a scanned
    PDF). A file that won't open at all is Rejected."""
    try:
        text = _pdf_text(data) if kind == "pdf" else _docx_text(data)
    except Rejected:
        raise
    except Exception as exc:
        # The parser's message can quote the file; log only what kind of failure it was.
        log.info("resume %s didn't parse: %s", kind, type(exc).__name__)
        raise Rejected(f"That {kind.upper()} couldn't be opened. Try saving it again, or export a fresh copy.") from exc
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return text[:MAX_TEXT]


def check(data: bytes) -> str:
    """The kind of an upload that may be stored, or Rejected with why not."""
    if not data:
        raise Rejected("That file is empty.")
    if len(data) > MAX_BYTES:
        raise Rejected("That file is over 5 MB. A resume is usually well under 1 MB; try exporting it again.")
    kind = sniff(data)
    if kind is None:
        raise Rejected("Only PDF and Word (.docx) resumes can be uploaded.")
    return kind


def store(db: Session, user: User, filename: str | None, data: bytes) -> Resume:
    """Check, read and save an upload as the user's newest (base) resume."""
    kind = check(data)
    resume = Resume(
        user_id=user.id,
        filename=clean_filename(filename, kind),
        kind=kind,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content=data,
        text=extract_text(kind, data),
    )
    db.add(resume)
    db.commit()
    return resume


def for_user(db: Session, user: User) -> list[Resume]:
    """The user's resumes, newest (the base resume) first. The file itself isn't
    needed for a list, so it isn't loaded."""
    query = (
        select(Resume)
        .options(defer(Resume.content))
        .where(Resume.user_id == user.id)
        .order_by(Resume.created_at.desc(), Resume.id.desc())
    )
    return list(db.scalars(query))


def get(db: Session, user: User, resume_id: int) -> Resume | None:
    """The user's resume with this id, or None, including when it's someone else's."""
    return db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id))
