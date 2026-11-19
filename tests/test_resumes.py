"""#23: uploading a resume. Real PDF and DOCX files, built here in memory, through
the real parsers; real Postgres. The CSRF token travels as a form field, as the
page sends it."""

import io
import zipfile

import docx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app import resumes
from app.db import SessionLocal
from app.models import Resume
from tests.conftest import csrf, login

TEXT = "Jordan Example. Staff Accountant, 2019-2024. Skills: reconciliation, audit."


def make_pdf(text: str = TEXT, pad: int = 0) -> bytes:
    """A one-page PDF with `text` on it. `pad` adds a comment of that many bytes,
    so a valid file can be made exactly as big as a test needs."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    if pad:
        out += b"%" + b"x" * pad + b"\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _pdf_of_size(size: int) -> bytes:
    """A valid PDF of exactly `size` bytes (the startxref number's width moves with the padding)."""
    pad = size - len(make_pdf()) - 2
    while len(data := make_pdf(pad=pad)) != size:
        pad += size - len(data)
    return data


def make_blank_pdf() -> bytes:
    """A PDF with a page and no text on it: what a scanned resume looks like to a parser."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_docx(text: str = TEXT) -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Python"
    table.rows[0].cells[1].text = "5 years"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def upload(client, data: bytes, name: str = "resume.pdf", content_type: str = "application/pdf"):
    return client.post(
        "/profile/resumes",
        data={"csrf_token": csrf(client)},
        files={"resume": (name, data, content_type)},
        follow_redirects=False,
    )


def _resumes_of(user) -> list[Resume]:
    with SessionLocal() as db:
        return list(db.scalars(select(Resume).where(Resume.user_id == user.id).order_by(Resume.id)))


@pytest.mark.criterion(23, "A user uploads a PDF resume")
def test_a_user_uploads_a_pdf_resume(logged_in, user):
    response = upload(logged_in, make_pdf())
    assert response.status_code == 303
    [resume] = _resumes_of(user)
    assert (resume.kind, resume.filename) == ("pdf", "resume.pdf")
    assert "Staff Accountant" in resume.text
    page = logged_in.get("/profile").text
    assert "resume.pdf" in page and "(base resume)" in page
    assert "is uploaded and is now your base resume" in page


@pytest.mark.criterion(23, "A user uploads a DOCX resume")
def test_a_user_uploads_a_docx_resume(logged_in, user):
    response = upload(logged_in, make_docx(), "cv.docx", resumes.MEDIA_TYPES["docx"])
    assert response.status_code == 303
    [resume] = _resumes_of(user)
    assert resume.kind == "docx"
    assert "Staff Accountant" in resume.text
    assert "Python | 5 years" in resume.text  # tables are read too


@pytest.mark.criterion(23, "Anything that isn't a PDF or DOCX is refused")
@pytest.mark.parametrize(
    ("name", "data", "content_type"),
    [
        ("resume.pdf", b"<html><script>alert(1)</script></html>", "application/pdf"),  # the name lies
        ("resume.docx", b"PK\x03\x04 not really a zip", resumes.MEDIA_TYPES["docx"]),
        ("resume.txt", TEXT.encode(), "text/plain"),
        ("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "image/png"),
    ],
)
def test_anything_that_isnt_a_pdf_or_docx_is_refused(logged_in, user, name, data, content_type):
    response = upload(logged_in, data, name, content_type)
    assert response.status_code == 400
    assert "Only PDF and Word (.docx) resumes can be uploaded." in response.text
    assert _resumes_of(user) == []


def test_a_zip_that_isnt_a_word_document_is_refused(logged_in, user):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("notes.txt", "hello")
    assert upload(logged_in, buffer.getvalue(), "resume.docx").status_code == 400
    assert _resumes_of(user) == []


def test_a_docx_that_unpacks_huge_is_refused_before_parsing(logged_in, user):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w/>" + " " * (60 * 1024 * 1024))  # 60 MB of spaces, a few KB packed
    data = buffer.getvalue()
    assert len(data) < resumes.MAX_BYTES
    response = upload(logged_in, data, "resume.docx")
    assert response.status_code == 400
    assert "unpacks to far more than a resume does" in response.text
    assert _resumes_of(user) == []


def test_a_pdf_that_wont_open_is_refused_with_a_message(logged_in, user):
    response = upload(logged_in, b"%PDF-1.4\n garbage with no objects at all")
    assert response.status_code == 400
    assert "That PDF couldn&#39;t be opened." in response.text
    assert _resumes_of(user) == []


@pytest.mark.criterion(23, "A file over the limit is refused")
def test_a_file_over_the_limit_is_refused(logged_in, user):
    exactly = _pdf_of_size(resumes.MAX_BYTES)
    assert upload(logged_in, exactly).status_code == 303  # the limit itself is allowed

    over = _pdf_of_size(resumes.MAX_BYTES + 1)
    response = upload(logged_in, over)
    assert response.status_code == 400
    assert "That file is over 5 MB." in response.text
    assert len(_resumes_of(user)) == 1


def test_an_empty_file_is_refused(logged_in, user):
    response = upload(logged_in, b"")
    assert response.status_code == 400
    assert _resumes_of(user) == []


def test_an_upload_needs_the_csrf_token(logged_in, user):
    response = logged_in.post("/profile/resumes", files={"resume": ("resume.pdf", make_pdf(), "application/pdf")})
    assert response.status_code == 403
    assert _resumes_of(user) == []


def test_an_upload_needs_a_login(client):
    response = client.post(
        "/profile/resumes", data={"csrf_token": csrf(client)}, files={"resume": ("r.pdf", make_pdf())}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.criterion(23, "Nobody else can fetch my resume")
def test_nobody_else_can_fetch_my_resume(client, make_user):
    owner, other = make_user(), make_user()
    login(client, owner.email)
    upload(client, make_pdf())
    [resume] = _resumes_of(owner)

    own = client.get(f"/profile/resumes/{resume.id}/file")
    assert own.status_code == 200
    assert own.content == make_pdf()
    assert own.headers["content-type"] == "application/pdf"
    assert own.headers["content-disposition"].startswith("attachment;")
    assert "no-store" in own.headers["cache-control"]

    client.cookies.clear()
    login(client, other.email)
    assert client.get(f"/profile/resumes/{resume.id}/file").status_code == 404
    assert client.get("/profile/resumes/999999999/file").status_code == 404  # same answer as a missing one
    assert "resume.pdf" not in client.get("/profile").text


@pytest.mark.criterion(23, "A PDF with no text layer is accepted and explained")
def test_a_pdf_with_no_text_is_accepted_and_explained(logged_in, user):
    response = upload(logged_in, make_blank_pdf(), "scan.pdf")
    assert response.status_code == 303
    [resume] = _resumes_of(user)
    assert resume.text == ""
    page = logged_in.get("/profile").text
    assert "it has no text in it (a scanned PDF?)" in page
    assert "No text found: a profile can't be built from this file." in page


def test_the_newest_upload_is_the_base_resume(logged_in, user):
    upload(logged_in, make_pdf(), "first.pdf")
    upload(logged_in, make_docx(), "second.docx")
    page = logged_in.get("/profile").text
    assert page.index("second.docx") < page.index("first.pdf")
    assert page.count("(base resume)") == 1
    assert page.index("(base resume)") < page.index("first.pdf")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\My Resume.pdf", "My Resume.pdf"),
        ("evil\x00\r\nname.pdf", "evilname.pdf"),
        ("", "resume.pdf"),
        (None, "resume.pdf"),
        ("a" * 300 + ".pdf", "a" * 255),
    ],
)
def test_a_filename_is_only_ever_a_plain_name(raw, expected):
    assert resumes.clean_filename(raw, "pdf") == expected


def test_a_filename_with_markup_is_shown_as_text(logged_in, user):
    upload(logged_in, make_pdf(), "<img src=x onerror=alert(1)>.pdf")
    page = logged_in.get("/profile").text
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;.pdf" in page
