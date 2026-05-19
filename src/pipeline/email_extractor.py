# ============================================================
# src/extractors/email_extractor.py
# Low-level email extractor
#
# Nhiệm vụ duy nhất:
#   - Đọc 1 file .eml
#   - Lấy metadata email
#   - Lấy body text
#   - Lấy attachment PDF
#   - Extract text từ PDF
# ============================================================

import re
import sys
import tempfile
from pathlib import Path
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr

import pdfplumber


try:
    from src.config.logging_config import get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.config.logging_config import get_logger


logger = get_logger(__name__)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value: str | None) -> str:
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def clean_multiline_text(value: str | None) -> str:
    if not value:
        return ""

    lines = [clean_text(line) for line in str(value).splitlines()]
    lines = [line for line in lines if line]

    return "\n".join(lines)


def sanitize_filename(filename: str) -> str:
    filename = clean_text(filename)
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)

    return filename


def parse_email_file(path: str | Path):
    """
    Đọc file .eml bằng email parser.
    """

    path = Path(path)

    with open(path, "rb") as f:
        return BytesParser(policy=policy.default).parse(f)


def parse_email_datetime(raw_date: str | None) -> str:
    """
    Parse Date header thành ISO datetime.
    """

    if not raw_date:
        return ""

    try:
        return parsedate_to_datetime(raw_date).isoformat()
    except Exception:
        return ""


def parse_email_date(raw_date: str | None) -> str:
    """
    Parse Date header thành YYYY-MM-DD.
    """

    if not raw_date:
        return ""

    try:
        return parsedate_to_datetime(raw_date).date().isoformat()
    except Exception:
        return ""


# ============================================================
# BODY EXTRACTION
# ============================================================

def strip_html(html: str) -> str:
    """
    Fallback đơn giản để lấy text từ HTML email.
    """

    if not html:
        return ""

    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</p\s*>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)

    html = (
        html.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )

    return clean_multiline_text(html)


def get_email_body(msg) -> str:
    """
    Lấy nội dung email.
    Ưu tiên text/plain, fallback text/html.
    """

    plain_parts = []
    html_parts = []

    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            continue

        content_type = part.get_content_type()

        if content_type not in {"text/plain", "text/html"}:
            continue

        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
            content = payload.decode("utf-8", errors="replace") if payload else ""

        if not content:
            continue

        if content_type == "text/plain":
            plain_parts.append(content)
        else:
            html_parts.append(strip_html(content))

    if plain_parts:
        return "\n".join(plain_parts)

    return "\n".join(html_parts)


# ============================================================
# PDF EXTRACTION
# ============================================================

def extract_pdf_text_from_bytes(pdf_bytes: bytes) -> tuple[str, str]:
    """
    Extract text từ PDF bytes.
    """

    if not pdf_bytes:
        return "", ""

    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)

        chunks = []

        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                chunks.append(text)

        return "\n".join(chunks), "pdfplumber"

    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


def extract_pdf_attachments(msg, extract_text: bool = True) -> list[dict]:
    """
    Lấy danh sách PDF attachment trong email.

    Returns:
        [
            {
                filename,
                content_type,
                size_bytes,
                text,
                extraction_method
            }
        ]
    """

    attachments = []

    for part in msg.walk():
        filename = part.get_filename()

        if not filename:
            continue

        if not filename.lower().endswith(".pdf"):
            continue

        payload = part.get_payload(decode=True)

        if not payload:
            continue

        safe_name = sanitize_filename(filename)

        pdf_text = ""
        extraction_method = ""

        if extract_text:
            pdf_text, extraction_method = extract_pdf_text_from_bytes(payload)

        attachments.append(
            {
                "filename": safe_name,
                "content_type": part.get_content_type(),
                "size_bytes": len(payload),
                "text": pdf_text,
                "extraction_method": extraction_method,
            }
        )

    return attachments


# ============================================================
# MAIN EXTRACT FUNCTION
# ============================================================

def extract_email(
    eml_path: str | Path,
    extract_pdf_text: bool = True,
) -> dict:
    """
    Extract raw email data từ 1 file .eml.

    Hàm này không xử lý nghiệp vụ.
    Không validate order.
    Không lookup DB.
    Không ghi file.
    """

    eml_path = Path(eml_path)

    logger.info("Extracting email: %s", eml_path.name)

    msg = parse_email_file(eml_path)

    raw_from = msg.get("From", "")
    from_name, from_address = parseaddr(raw_from)

    raw_date = msg.get("Date", "")

    body = get_email_body(msg)
    attachments = extract_pdf_attachments(
        msg=msg,
        extract_text=extract_pdf_text,
    )

    result = {
        "source_email_file": eml_path.name,
        "source_email_path": str(eml_path),
        "message_id": clean_text(msg.get("Message-ID", "")),
        "subject": clean_text(msg.get("Subject", "")),
        "from_name": clean_text(from_name),
        "from_address": clean_text(from_address),
        "to": clean_text(msg.get("To", "")),
        "raw_date": clean_text(raw_date),
        "received_at": parse_email_datetime(raw_date),
        "email_date": parse_email_date(raw_date),
        "body": body,
        "attachments": attachments,
    }

    logger.info(
        "Email extracted | file=%s | message_id=%s | pdf_count=%s",
        eml_path.name,
        result["message_id"],
        len(attachments),
    )

    return result