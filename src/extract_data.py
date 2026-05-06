from pathlib import Path
import csv
import os
import re
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from collections import Counter

from dotenv import load_dotenv
import pdfplumber
import psycopg2


# Load biến môi trường từ file .env
load_dotenv()


RAW_EMAIL_DIR = Path("data/raw/tnbike_emails_mar2026")

OUT_DIR = Path("data/staging")
OUT_EMAIL_LOG = OUT_DIR / "staging_email_log.csv"
OUT_SALES_ORDER = OUT_DIR / "staging_sales_order.csv"
OUT_ORDER_LINE = OUT_DIR / "staging_order_line.csv"
OUT_FAILED = OUT_DIR / "staging_fail.csv"
OUT_FAILED_SUMMARY = OUT_DIR / "staging_fail_summary.csv"
OUT_MISSING_CUSTOMER = OUT_DIR / "missing_customer.csv"
OUT_MISSING_PRODUCT = OUT_DIR / "missing_product.csv"


# ============================================================
# DB config from .env / environment
# ============================================================

def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Thiếu biến môi trường: {name}. "
            f"Hãy kiểm tra file .env ở root project."
        )

    return value


DB_CONFIG = {
    "host": get_required_env("PGHOST"),
    "port": os.getenv("PGPORT", "5432"),
    "database": get_required_env("PGDATABASE"),
    "user": get_required_env("PGUSER"),
    "password": get_required_env("PGPASSWORD"),
}


# ============================================================
# Helpers
# ============================================================

def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_money(value: str | None) -> Decimal:
    """
    1.898.148 -> Decimal("1898148")
    """
    if not value:
        return Decimal("0")

    cleaned = (
        value.strip()
        .replace(".", "")
        .replace(",", "")
        .replace(" ", "")
    )

    return Decimal(cleaned or "0")


def parse_quantity(value: str | None) -> Decimal:
    """
    1   -> Decimal("1")
    1,5 -> Decimal("1.5")
    """
    if not value:
        return Decimal("0")

    return Decimal(value.strip().replace(",", "."))


def decimal_to_str(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def money_to_str(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def none_to_empty(value) -> str:
    return "" if value is None else str(value)


# ============================================================
# DB READ-ONLY lookup
# ============================================================

def load_customer_lookup_from_db() -> dict[str, str]:
    """
    READ ONLY:
    customer.tax_code -> customer.customer_code
    """

    sql = """
        SELECT tax_code, customer_code
        FROM tnbike.customer
        WHERE tax_code IS NOT NULL
          AND customer_code IS NOT NULL;
    """

    lookup = {}

    with psycopg2.connect(**DB_CONFIG) as conn:
        conn.set_session(readonly=True, autocommit=True)

        with conn.cursor() as cur:
            cur.execute(sql)

            for tax_code, customer_code in cur.fetchall():
                tax_code = clean_text(tax_code)
                customer_code = clean_text(customer_code)

                if tax_code and customer_code:
                    lookup[tax_code] = customer_code

    return lookup


def load_product_codes_from_db() -> set[str]:
    """
    READ ONLY:
    Lấy product.product_code để validate SKU.
    """

    sql = """
        SELECT product_code
        FROM tnbike.product;
    """

    product_codes = set()

    with psycopg2.connect(**DB_CONFIG) as conn:
        conn.set_session(readonly=True, autocommit=True)

        with conn.cursor() as cur:
            cur.execute(sql)

            for (product_code,) in cur.fetchall():
                product_codes.add(clean_text(product_code).upper())

    return product_codes


# ============================================================
# Email extraction
# ============================================================

def parse_email(path: Path):
    with open(path, "rb") as f:
        return BytesParser(policy=policy.default).parse(f)


def get_email_body(msg) -> str:
    body_parts = []

    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            continue

        if part.get_content_type() == "text/plain":
            try:
                body_parts.append(part.get_content())
            except Exception:
                payload = part.get_payload(decode=True)

                if payload:
                    body_parts.append(payload.decode("utf-8", errors="replace"))

    return "\n".join(body_parts)


def get_email_header_date(msg) -> str:
    """
    Chỉ lấy order_date từ email header Date.

    Date: Sun, 01 Mar 2026 14:28:41 +0700
    -> 2026-03-01
    """

    raw_date = msg.get("Date")

    if not raw_date:
        return ""

    try:
        dt = parsedate_to_datetime(raw_date)
        return dt.date().isoformat()
    except Exception:
        return ""


def get_email_received_at(msg) -> str:
    raw_date = msg.get("Date")

    if not raw_date:
        return ""

    try:
        return parsedate_to_datetime(raw_date).isoformat()
    except Exception:
        return ""


def extract_pdf_attachment(msg, output_dir: Path) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)

    for part in msg.walk():
        filename = part.get_filename()

        if not filename:
            continue

        if not filename.lower().endswith(".pdf"):
            continue

        payload = part.get_payload(decode=True)

        if not payload:
            continue

        pdf_path = output_dir / filename

        with open(pdf_path, "wb") as f:
            f.write(payload)

        return pdf_path

    return None


def build_email_log_row(
    msg,
    attachment_name: str = "",
    processing_status: str = "parsed",
) -> dict:
    raw_from = msg.get("From", "")
    _, from_address = parseaddr(raw_from)

    return {
        "message_id": clean_text(msg.get("Message-ID", "")),
        "from_address": clean_text(from_address),
        "received_at": get_email_received_at(msg),
        "attachment_name": attachment_name,
        "processing_status": processing_status,
    }


# ============================================================
# PDF extraction
# ============================================================

def extract_pdf_text(pdf_path: Path) -> str:
    chunks = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            chunks.append(text)

    return "\n".join(chunks)


# ============================================================
# Header parsing
# ============================================================

def normalize_so_number(value: str | None) -> str:
    """
    BH26.0935 / BH26_0935 / BH26-0935 -> BH26.0935
    """

    if not value:
        return ""

    value = value.upper().strip()
    value = value.replace("_", ".").replace("-", ".")

    match = re.search(r"BH\d{2}\.\d{4}", value)
    return match.group(0) if match else ""


def extract_so_number(source: str) -> str:
    match = re.search(r"BH\d{2}[._-]\d{4}", source, flags=re.IGNORECASE)

    if not match:
        return ""

    return normalize_so_number(match.group(0))


def infer_invoice_symbol(so_number: str) -> str:
    """
    BH26.0935 -> C26TTN
    BH25.0123 -> C25TTN
    """

    match = re.search(r"BH(\d{2})\.", so_number)

    if not match:
        return ""

    return f"C{match.group(1)}TTN"


def extract_tax_code(source: str) -> str:
    match = re.search(
        r"\bMST\s*[:\-]?\s*(\d{8,15})\b",
        source,
        flags=re.IGNORECASE,
    )

    return match.group(1) if match else ""


def extract_customer_name(source: str) -> str:
    """
    Cố gắng lấy tên đại lý để hỗ trợ missing_customer.csv.
    Không dùng làm FK.
    """

    patterns = [
        r"(?:Đại lý|Dai ly)\s*[:\-]\s*(.+?)(?:\s+MST\b|\n)",
        r"(?:Đại lý|Dai ly)\s*[:\-]\s*(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))

    return ""


def extract_order_header(
    email_subject: str,
    email_body: str,
    pdf_text: str,
    email_header_date: str,
) -> dict:
    source = "\n".join([email_subject, email_body, pdf_text])

    so_number = extract_so_number(source)

    return {
        "so_number": so_number,
        "invoice_symbol": infer_invoice_symbol(so_number),
        "invoice_number": "",              # NULL/rỗng theo flow đã chốt
        "order_date": email_header_date,   # Chỉ lấy từ header Date
        "tax_code": extract_tax_code(source),
        "customer_name_raw": extract_customer_name(source),
    }


# ============================================================
# Order line parsing
# ============================================================

def extract_order_lines(pdf_text: str) -> list[dict]:
    """
    Hỗ trợ product_code dạng:
    - 000104002009000
    - TP0099.0000570
    - TP0023.02.25.00
    - 156.01.12.0003

    Logic:
    - Bắt dòng bắt đầu bằng STT + product_code.
    - 3 cụm số cuối là quantity, unit_price, line_total.
    """

    rows = []

    for raw_line in pdf_text.splitlines():
        line = clean_text(raw_line)

        match = re.match(
            r"^\s*(\d+)\s+([A-Z0-9.]{8,25})\s+(.+)$",
            line,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        stt = match.group(1)
        product_code = match.group(2).upper()
        rest = match.group(3)

        numbers = re.findall(r"\d+(?:[,.]\d+)*", rest)

        if len(numbers) < 3:
            rows.append(
                {
                    "stt": stt,
                    "product_code": product_code,
                    "quantity": None,
                    "unit_price": None,
                    "line_total": None,
                    "raw_line": line,
                    "parse_error": "Không đủ 3 số cuối để lấy quantity, unit_price, line_total",
                    "warning": "",
                }
            )
            continue

        try:
            quantity = parse_quantity(numbers[-3])
            unit_price = parse_money(numbers[-2])
            line_total = parse_money(numbers[-1])
        except Exception as e:
            rows.append(
                {
                    "stt": stt,
                    "product_code": product_code,
                    "quantity": None,
                    "unit_price": None,
                    "line_total": None,
                    "raw_line": line,
                    "parse_error": f"Lỗi parse số: {e}",
                    "warning": "",
                }
            )
            continue

        calculated_total = (quantity * unit_price).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )

        warning = ""

        if line_total <= 0:
            line_total = calculated_total
            warning = "line_total <= 0 nên dùng quantity * unit_price"

        elif abs(line_total - calculated_total) > Decimal("1"):
            # Chỉ warning, không đưa vào staging_fail.
            # Giữ line_total theo PDF.
            warning = (
                f"line_total lệch calculated_total: "
                f"pdf={line_total}, calculated={calculated_total}"
            )

        rows.append(
            {
                "stt": stt,
                "product_code": product_code,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "raw_line": line,
                "parse_error": "",
                "warning": warning,
            }
        )

    return rows


# ============================================================
# Validation
# ============================================================

def validate_header(header: dict, customer_code: str) -> list[str]:
    errors = []

    if not header["so_number"]:
        errors.append("Không trích xuất được so_number")

    if not header["order_date"]:
        errors.append("Không lấy được order_date từ email header Date")

    if not header["tax_code"]:
        errors.append("Không trích xuất được MST")

    if not customer_code:
        errors.append(f"Không map được customer_code từ MST={header['tax_code']}")

    return errors


def validate_order_line(line: dict, product_codes: set[str]) -> list[str]:
    errors = []

    if line.get("parse_error"):
        errors.append(line["parse_error"])

    product_code = clean_text(line.get("product_code")).upper()
    quantity = line.get("quantity")
    unit_price = line.get("unit_price")
    line_total = line.get("line_total")

    if not product_code:
        errors.append("Thiếu product_code")
    elif product_code not in product_codes:
        errors.append(f"product_code không tồn tại trong DB: {product_code}")

    if quantity is None:
        errors.append("Thiếu quantity")
    elif quantity <= 0:
        errors.append("quantity <= 0")

    if unit_price is None:
        errors.append("Thiếu unit_price")
    elif unit_price < 0:
        errors.append("unit_price < 0")

    if line_total is None:
        errors.append("Thiếu line_total")
    elif line_total <= 0:
        errors.append("line_total <= 0")

    return errors


# ============================================================
# Parse one email
# ============================================================

def parse_email_file(
    eml_path: Path,
    customer_lookup: dict[str, str],
    product_codes: set[str],
) -> tuple[dict | None, list[dict], list[dict], dict, list[dict], list[dict]]:
    """
    Return:
    - sales_order_row hoặc None
    - valid_order_line_rows
    - fail_rows
    - email_log_row
    - missing_customer_rows
    - missing_product_rows

    Rule:
    - Customer/header lỗi: KHÔNG ghi sales_order, KHÔNG ghi order_line.
    - Product lỗi: KHÔNG ghi order_line lỗi đó.
    - Nếu sau khi lọc product lỗi mà không còn order_line hợp lệ: KHÔNG ghi sales_order.
    - line_total lệch calculated_total: bỏ qua lỗi, vẫn ghi order_line hợp lệ.
    """

    fail_rows = []
    missing_customer_rows = []
    missing_product_rows = []

    msg = parse_email(eml_path)

    email_subject = clean_text(msg.get("Subject", ""))
    email_body = get_email_body(msg)
    email_header_date = get_email_header_date(msg)

    attachment_name = ""
    email_log_row = build_email_log_row(
        msg=msg,
        attachment_name=attachment_name,
        processing_status="started",
    )

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = extract_pdf_attachment(msg, Path(tmp))

        if not pdf_path:
            fail_rows.append(
                {
                    "record_type": "file",
                    "source_email_file": eml_path.name,
                    "so_number": "",
                    "stt": "",
                    "product_code": "",
                    "error": "Không tìm thấy PDF đính kèm",
                    "raw_line": "",
                }
            )

            email_log_row.update(
                {
                    "attachment_name": "",
                    "processing_status": "failed_no_pdf_attachment",
                }
            )

            return None, [], fail_rows, email_log_row, missing_customer_rows, missing_product_rows

        attachment_name = pdf_path.name
        pdf_text = extract_pdf_text(pdf_path)

    header = extract_order_header(
        email_subject=email_subject,
        email_body=email_body,
        pdf_text=pdf_text,
        email_header_date=email_header_date,
    )

    customer_code = customer_lookup.get(header["tax_code"], "")
    parsed_lines = extract_order_lines(pdf_text)

    header_errors = validate_header(header, customer_code)

    # ============================================================
    # Customer/header lỗi:
    # - ghi fail
    # - ghi missing_customer nếu thiếu customer_code
    # - block toàn bộ order_line
    # - return sales_order_row = None
    # ============================================================

    if not customer_code:
        missing_customer_rows.append(
            {
                "tax_code": header["tax_code"],
                "customer_name_raw": header.get("customer_name_raw", ""),
                "so_number": header["so_number"],
                "order_date": header["order_date"],
                "source_email_file": eml_path.name,
                "email_subject": email_subject,
                "line_count_parsed": str(len(parsed_lines)),
            }
        )

    if header_errors:
        header_error_text = " | ".join(header_errors)

        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": eml_path.name,
                "so_number": header["so_number"],
                "stt": "",
                "product_code": "",
                "error": header_error_text,
                "raw_line": "",
            }
        )

        if parsed_lines:
            for line in parsed_lines:
                line_errors = validate_order_line(line, product_codes)

                error_parts = [f"Sales_order lỗi nên không ghi sales_order/order_line: {header_error_text}"]

                if line_errors:
                    error_parts.append("Order_line cũng lỗi: " + " | ".join(line_errors))

                if line.get("warning"):
                    error_parts.append("Order_line warning ignored: " + line.get("warning", ""))

                if any("product_code không tồn tại trong DB:" in err for err in line_errors):
                    missing_product_rows.append(
                        {
                            "product_code": line.get("product_code", ""),
                            "so_number": header["so_number"],
                            "stt": line.get("stt", ""),
                            "quantity": decimal_to_str(line.get("quantity")),
                            "unit_price": money_to_str(line.get("unit_price")),
                            "line_total": money_to_str(line.get("line_total")),
                            "source_email_file": eml_path.name,
                            "raw_line": line.get("raw_line", ""),
                        }
                    )

                fail_rows.append(
                    {
                        "record_type": "order_line_blocked_by_sales_order_error",
                        "source_email_file": eml_path.name,
                        "so_number": header["so_number"],
                        "stt": line.get("stt", ""),
                        "product_code": line.get("product_code", ""),
                        "error": " | ".join(error_parts),
                        "raw_line": line.get("raw_line", ""),
                    }
                )
        else:
            fail_rows.append(
                {
                    "record_type": "order_line_blocked_by_sales_order_error",
                    "source_email_file": eml_path.name,
                    "so_number": header["so_number"],
                    "stt": "",
                    "product_code": "",
                    "error": (
                        f"Sales_order lỗi nên không ghi sales_order/order_line: {header_error_text} | "
                        "Không trích xuất được dòng hàng nào từ PDF"
                    ),
                    "raw_line": "\n".join(pdf_text.splitlines()[:20]),
                }
            )

        email_log_row.update(
            {
                "attachment_name": attachment_name,
                "processing_status": "failed_sales_order_validation",
            }
        )

        return None, [], fail_rows, email_log_row, missing_customer_rows, missing_product_rows

    # ============================================================
    # Header/customer hợp lệ, nhưng không parse được dòng hàng
    # → không ghi sales_order vì không có dòng order_line hợp lệ
    # ============================================================

    if not parsed_lines:
        fail_rows.append(
            {
                "record_type": "order_line",
                "source_email_file": eml_path.name,
                "so_number": header["so_number"],
                "stt": "",
                "product_code": "",
                "error": "Không trích xuất được dòng hàng nào từ PDF nên không ghi sales_order/order_line",
                "raw_line": "\n".join(pdf_text.splitlines()[:20]),
            }
        )

        email_log_row.update(
            {
                "attachment_name": attachment_name,
                "processing_status": "failed_no_order_line",
            }
        )

        return None, [], fail_rows, email_log_row, missing_customer_rows, missing_product_rows

    # ============================================================
    # Header/customer hợp lệ:
    # - product lỗi: không ghi line đó vào order_line
    # - line hợp lệ: ghi vào order_line
    # ============================================================

    valid_order_line_rows = []

    for line in parsed_lines:
        line_errors = validate_order_line(line, product_codes)

        if line_errors:
            fail_rows.append(
                {
                    "record_type": "order_line",
                    "source_email_file": eml_path.name,
                    "so_number": header["so_number"],
                    "stt": line.get("stt", ""),
                    "product_code": line.get("product_code", ""),
                    "error": " | ".join(line_errors),
                    "raw_line": line.get("raw_line", ""),
                }
            )

            if any("product_code không tồn tại trong DB:" in err for err in line_errors):
                missing_product_rows.append(
                    {
                        "product_code": line.get("product_code", ""),
                        "so_number": header["so_number"],
                        "stt": line.get("stt", ""),
                        "quantity": decimal_to_str(line.get("quantity")),
                        "unit_price": money_to_str(line.get("unit_price")),
                        "line_total": money_to_str(line.get("line_total")),
                        "source_email_file": eml_path.name,
                        "raw_line": line.get("raw_line", ""),
                    }
                )

            continue

        valid_order_line_rows.append(
            {
                "order_id": "",
                "so_number": header["so_number"],
                "product_code": line["product_code"],
                "quantity": decimal_to_str(line["quantity"]),
                "unit_price": money_to_str(line["unit_price"]),
                "line_total": money_to_str(line["line_total"]),
            }
        )

    # ============================================================
    # Nếu tất cả order_line đều lỗi product/validate
    # → không ghi sales_order để tránh order không có dòng hàng
    # ============================================================

    if not valid_order_line_rows:
        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": eml_path.name,
                "so_number": header["so_number"],
                "stt": "",
                "product_code": "",
                "error": "Không còn order_line hợp lệ sau khi lọc lỗi product/validate nên không ghi sales_order",
                "raw_line": "",
            }
        )

        email_log_row.update(
            {
                "attachment_name": attachment_name,
                "processing_status": "failed_no_valid_order_line",
            }
        )

        return None, [], fail_rows, email_log_row, missing_customer_rows, missing_product_rows

    # Chỉ tạo sales_order khi customer hợp lệ và có ít nhất 1 order_line hợp lệ.
    sales_order_row = {
        "so_number": header["so_number"],
        "invoice_symbol": header["invoice_symbol"],
        "invoice_number": header["invoice_number"],
        "order_date": header["order_date"],
        "customer_code": customer_code,
    }

    email_log_row.update(
        {
            "attachment_name": attachment_name,
            "processing_status": "success" if not fail_rows else "parsed_with_issues",
        }
    )

    return sales_order_row, valid_order_line_rows, fail_rows, email_log_row, missing_customer_rows, missing_product_rows

# ============================================================
# Fail summary
# ============================================================

def normalize_error(error: str) -> str:
    """
    Gom nhóm lỗi để summary không bị tách nhỏ theo MST, product_code, số tiền.
    """

    error = clean_text(error)

    if not error:
        return ""

    if "Trùng so_number trong batch" in error:
        return "Trùng so_number trong batch"

    if "Không tìm thấy PDF đính kèm" in error:
        return "Không tìm thấy PDF đính kèm"

    if "Không trích xuất được so_number" in error:
        return "Không trích xuất được so_number"

    if "Không lấy được order_date từ email header Date" in error:
        return "Không lấy được order_date từ email header Date"

    if "Không trích xuất được MST" in error:
        return "Không trích xuất được MST"

    if "Không map được customer_code từ MST=" in error:
        return "Không map được customer_code từ MST"

    if "Sales_order lỗi:" in error and "Không map được customer_code từ MST=" in error:
        if "Không trích xuất được dòng hàng nào từ PDF" in error:
            return "Sales_order lỗi do không map customer_code + không parse được order_line"
        return "Sales_order lỗi do không map được customer_code"

    if "Sales_order lỗi:" in error:
        return "Sales_order lỗi"

    if "Order_line cũng lỗi:" in error and "product_code không tồn tại trong DB:" in error:
        return "Order_line bị chặn do sales_order lỗi + product_code không tồn tại trong DB"

    if "product_code không tồn tại trong DB:" in error:
        return "product_code không tồn tại trong DB"

    if "Không đủ 3 số cuối" in error:
        return "Không đủ 3 số cuối để lấy quantity, unit_price, line_total"

    if "Lỗi parse số:" in error:
        return "Lỗi parse số"

    if "Thiếu product_code" in error:
        return "Thiếu product_code"

    if "Thiếu quantity" in error:
        return "Thiếu quantity"

    if "quantity <= 0" in error:
        return "quantity <= 0"

    if "Thiếu unit_price" in error:
        return "Thiếu unit_price"

    if "unit_price < 0" in error:
        return "unit_price < 0"

    if "Thiếu line_total" in error:
        return "Thiếu line_total"

    if "line_total <= 0" in error:
        return "line_total <= 0"

    if "line_total lệch calculated_total" in error:
        return "line_total lệch calculated_total"

    if "Không trích xuất được dòng hàng nào từ PDF" in error:
        return "Không trích xuất được dòng hàng nào từ PDF"

    return error


def summarize_fail_rows(fail_rows: list[dict]) -> list[dict]:
    """
    Thống kê lỗi đã gom nhóm.

    Output:
    - group_type = record_type hoặc error_group
    - group_value = nhóm lỗi
    - count
    """

    summary_rows = []

    by_record_type = Counter(row.get("record_type", "") for row in fail_rows)
    by_error_group = Counter(normalize_error(row.get("error", "")) for row in fail_rows)

    for record_type, count in by_record_type.most_common():
        summary_rows.append(
            {
                "group_type": "record_type",
                "group_value": record_type,
                "count": count,
            }
        )

    for error_group, count in by_error_group.most_common():
        summary_rows.append(
            {
                "group_type": "error_group",
                "group_value": error_group,
                "count": count,
            }
        )

    return summary_rows


def summarize_missing_customers(rows: list[dict]) -> list[dict]:
    grouped = {}

    for row in rows:
        key = row.get("tax_code", "")
        if key not in grouped:
            grouped[key] = {
                "tax_code": row.get("tax_code", ""),
                "customer_name_raw": row.get("customer_name_raw", ""),
                "affected_orders": 0,
                "affected_lines_parsed": 0,
                "example_so_number": row.get("so_number", ""),
                "example_email_file": row.get("source_email_file", ""),
                "example_subject": row.get("email_subject", ""),
            }

        grouped[key]["affected_orders"] += 1
        grouped[key]["affected_lines_parsed"] += int(row.get("line_count_parsed") or 0)

    return list(grouped.values())


def summarize_missing_products(rows: list[dict]) -> list[dict]:
    grouped = {}

    for row in rows:
        key = row.get("product_code", "")

        if key not in grouped:
            grouped[key] = {
                "product_code": key,
                "affected_lines": 0,
                "total_quantity": Decimal("0"),
                "total_line_total": Decimal("0"),
                "example_so_number": row.get("so_number", ""),
                "example_email_file": row.get("source_email_file", ""),
                "example_raw_line": row.get("raw_line", ""),
            }

        grouped[key]["affected_lines"] += 1
        grouped[key]["total_quantity"] += parse_quantity(row.get("quantity", "0") or "0")
        grouped[key]["total_line_total"] += parse_money(row.get("line_total", "0") or "0")

    output = []

    for item in grouped.values():
        output.append(
            {
                "product_code": item["product_code"],
                "affected_lines": str(item["affected_lines"]),
                "total_quantity": decimal_to_str(item["total_quantity"]),
                "total_line_total": money_to_str(item["total_line_total"]),
                "example_so_number": item["example_so_number"],
                "example_email_file": item["example_email_file"],
                "example_raw_line": item["example_raw_line"],
            }
        )

    return output


# ============================================================
# CSV writer
# ============================================================

def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                {key: none_to_empty(row.get(key)) for key in fieldnames}
            )


# ============================================================
# Main
# ============================================================

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    customer_lookup = load_customer_lookup_from_db()
    product_codes = load_product_codes_from_db()

    email_log_rows = []
    sales_order_rows = []
    order_line_rows = []
    fail_rows = []
    missing_customer_raw_rows = []
    missing_product_raw_rows = []

    eml_files = sorted(RAW_EMAIL_DIR.glob("*.eml"))

    if not eml_files:
        print(f"[WARN] Không tìm thấy file .eml trong {RAW_EMAIL_DIR}")

    seen_so_numbers = set()

    for eml_path in eml_files:
        try:
            (
                sales_order_row,
                valid_line_rows,
                file_fail_rows,
                email_log_row,
                file_missing_customers,
                file_missing_products,
            ) = parse_email_file(
                eml_path=eml_path,
                customer_lookup=customer_lookup,
                product_codes=product_codes,
            )

            fail_rows.extend(file_fail_rows)
            missing_customer_raw_rows.extend(file_missing_customers)
            missing_product_raw_rows.extend(file_missing_products)

            if email_log_row:
                email_log_rows.append(email_log_row)

            if sales_order_row:
                so_number = sales_order_row.get("so_number", "")

                if so_number and so_number in seen_so_numbers:
                    fail_rows.append(
                        {
                            "record_type": "sales_order",
                            "source_email_file": eml_path.name,
                            "so_number": so_number,
                            "stt": "",
                            "product_code": "",
                            "error": f"Trùng so_number trong batch: {so_number}",
                            "raw_line": "",
                        }
                    )

                    for line_row in valid_line_rows:
                        fail_rows.append(
                            {
                                "record_type": "order_line_blocked_by_sales_order_error",
                                "source_email_file": eml_path.name,
                                "so_number": so_number,
                                "stt": "",
                                "product_code": line_row.get("product_code", ""),
                                "error": f"Sales_order lỗi: Trùng so_number trong batch: {so_number}",
                                "raw_line": "",
                            }
                        )

                    valid_line_rows = []

                else:
                    if so_number:
                        seen_so_numbers.add(so_number)

                    sales_order_rows.append(sales_order_row)

            order_line_rows.extend(valid_line_rows)

        except Exception as e:
            fail_rows.append(
                {
                    "record_type": "file",
                    "source_email_file": eml_path.name,
                    "so_number": "",
                    "stt": "",
                    "product_code": "",
                    "error": str(e),
                    "raw_line": "",
                }
            )

    fail_summary_rows = summarize_fail_rows(fail_rows)
    missing_customer_rows = summarize_missing_customers(missing_customer_raw_rows)
    missing_product_rows = summarize_missing_products(missing_product_raw_rows)

    write_csv(
        OUT_EMAIL_LOG,
        [
            "message_id",
            "from_address",
            "received_at",
            "attachment_name",
            "processing_status",
        ],
        email_log_rows,
    )

    write_csv(
        OUT_SALES_ORDER,
        [
            "so_number",
            "invoice_symbol",
            "invoice_number",
            "order_date",
            "customer_code",
        ],
        sales_order_rows,
    )

    write_csv(
        OUT_ORDER_LINE,
        [
            "order_id",
            "so_number",
            "product_code",
            "quantity",
            "unit_price",
            "line_total",
        ],
        order_line_rows,
    )

    write_csv(
        OUT_FAILED,
        [
            "record_type",
            "source_email_file",
            "so_number",
            "stt",
            "product_code",
            "error",
            "raw_line",
        ],
        fail_rows,
    )

    write_csv(
        OUT_FAILED_SUMMARY,
        [
            "group_type",
            "group_value",
            "count",
        ],
        fail_summary_rows,
    )

    write_csv(
        OUT_MISSING_CUSTOMER,
        [
            "tax_code",
            "customer_name_raw",
            "affected_orders",
            "affected_lines_parsed",
            "example_so_number",
            "example_email_file",
            "example_subject",
        ],
        missing_customer_rows,
    )

    write_csv(
        OUT_MISSING_PRODUCT,
        [
            "product_code",
            "affected_lines",
            "total_quantity",
            "total_line_total",
            "example_so_number",
            "example_email_file",
            "example_raw_line",
        ],
        missing_product_rows,
    )

    print(f"Email log        : {len(email_log_rows)} -> {OUT_EMAIL_LOG}")
    print(f"Sales orders     : {len(sales_order_rows)} -> {OUT_SALES_ORDER}")
    print(f"Order line rows  : {len(order_line_rows)} -> {OUT_ORDER_LINE}")
    print(f"Fail rows        : {len(fail_rows)} -> {OUT_FAILED}")
    print(f"Fail summary     : {len(fail_summary_rows)} -> {OUT_FAILED_SUMMARY}")
    print(f"Missing customer : {len(missing_customer_rows)} -> {OUT_MISSING_CUSTOMER}")
    print(f"Missing product  : {len(missing_product_rows)} -> {OUT_MISSING_PRODUCT}")


if __name__ == "__main__":
    main()