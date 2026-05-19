# ============================================================
# src/pipeline/extract_to_staging.py
# Convert extracted emails -> staging CSV
#
# Input:
#   data/incoming/eml/*.eml
#
# Output staging - dữ liệu chuẩn bị load DB:
#   data/processed/staging/staging_email_log.csv
#   data/processed/staging/staging_customer.csv
#   data/processed/staging/staging_sales_order.csv
#   data/processed/staging/staging_order_line.csv
#
# Output quality check - dữ liệu kiểm tra lỗi:
#   data/processed/quality_check/extract_fail.csv
#   data/processed/quality_check/extract_fail_summary.csv
# ============================================================

import re
import csv
import sys
import argparse
import unicodedata
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from collections import Counter


try:
    from src.pipeline.email_extractor import extract_email
    from src.database.connection import get_cursor, DB_SCHEMA
    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.pipeline.email_extractor import extract_email
    from src.database.connection import get_cursor, DB_SCHEMA
    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger


logger = get_logger(__name__)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_INPUT_DIR = "data/incoming/eml"
DEFAULT_STAGING_DIR = "data/processed/staging"
DEFAULT_QUALITY_CHECK_DIR = "data/processed/quality_check"

OUT_EMAIL_LOG = "staging_email_log.csv"
OUT_STAGING_CUSTOMER = "staging_customer.csv"
OUT_SALES_ORDER = "staging_sales_order.csv"
OUT_ORDER_LINE = "staging_order_line.csv"

OUT_EXTRACT_FAIL = "extract_fail.csv"
OUT_EXTRACT_FAIL_SUMMARY = "extract_fail_summary.csv"


# ============================================================
# STATUS / REASON
# ============================================================

STATUS_PROCESSING = "PROCESSING"
STATUS_SUCCESS = "SUCCESS"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_FAILED = "FAILED"

REASON_PROCESSING = "PROCESSING"
REASON_SUCCESS = "SUCCESS"
REASON_NEW_CUSTOMER = "NEW_CUSTOMER"
REASON_LINE_ERROR = "LINE_ERROR"
REASON_NEW_CUSTOMER_LINE_ERROR = "NEW_CUSTOMER+LINE_ERROR"
REASON_MISSING_PDF = "MISSING_PDF"
REASON_HEADER_ERROR = "HEADER_ERROR"
REASON_NO_LINES = "NO_LINES"
REASON_NO_VALID_LINES = "NO_VALID_LINES"
REASON_CUSTOMER_ERROR = "CUSTOMER_ERROR"
REASON_DUPLICATE_SO = "DUPLICATE_SO"
REASON_EXCEPTION = "EXCEPTION"


# ============================================================
# BASIC HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def none_to_empty(value) -> str:
    return "" if value is None else str(value)


def parse_money(value: str | None) -> Decimal:
    if not value:
        return Decimal("0")

    cleaned = (
        str(value)
        .strip()
        .replace(".", "")
        .replace(",", "")
        .replace(" ", "")
    )

    return Decimal(cleaned or "0")


def parse_quantity(value: str | None) -> Decimal:
    if not value:
        return Decimal("0")

    return Decimal(str(value).strip().replace(",", "."))


def decimal_to_str(value: Decimal | None) -> str:
    if value is None:
        return ""

    return format(value.normalize(), "f")


def money_to_str(value: Decimal | None) -> str:
    if value is None:
        return ""

    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_vietnamese_text(value: str | None) -> str:
    value = clean_text(value).lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9\s]", " ", value)

    return re.sub(r"\s+", " ", value).strip()


def looks_like_bad_customer_name(value: str) -> bool:
    value = clean_text(value)
    normalized = normalize_vietnamese_text(value)

    if not value:
        return True

    if len(value) < 5:
        return True

    bad_tokens = [
        "file pdf",
        "tong",
        "tong gia tri",
        "mong som",
        "kinh gui",
        "phong kinh doanh",
        "don hang",
        "purchase order",
        "dia chi",
        "lien he",
    ]

    if any(token in normalized for token in bad_tokens):
        return True

    if re.fullmatch(r"[0-9\s.\-]+", value):
        return True

    return False


# ============================================================
# DB LOOKUP - READ ONLY
# ============================================================

def load_customer_lookup_from_db() -> dict[str, str]:
    query = """
        SELECT tax_code, customer_code
        FROM customer
        WHERE tax_code IS NOT NULL
          AND customer_code IS NOT NULL;
    """

    lookup = {}

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    for row in rows:
        tax_code = clean_text(row["tax_code"])
        customer_code = clean_text(row["customer_code"])

        if tax_code and customer_code:
            lookup[tax_code] = customer_code

    return lookup


def load_product_codes_from_db() -> set[str]:
    query = """
        SELECT product_code
        FROM product;
    """

    product_codes = set()

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    for row in rows:
        product_code = clean_text(row["product_code"]).upper()

        if product_code:
            product_codes.add(product_code)

    return product_codes


def load_next_customer_sequence_from_db() -> int:
    query = """
        SELECT customer_code
        FROM customer
        WHERE customer_code IS NOT NULL;
    """

    max_no = 0

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    for row in rows:
        customer_code = clean_text(row["customer_code"])
        match = re.search(r"KH-(\d+)", customer_code, flags=re.IGNORECASE)

        if match:
            max_no = max(max_no, int(match.group(1)))

    return max_no + 1


def load_province_lookup_from_db() -> dict[str, int]:
    query = """
        SELECT province_id, province_name
        FROM province;
    """

    lookup = {}

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    for row in rows:
        province_id = row["province_id"]
        province_name = row["province_name"]

        normalized_name = normalize_vietnamese_text(province_name)

        if normalized_name:
            lookup[normalized_name] = province_id

    return lookup


def generate_customer_code(seq: int) -> str:
    return f"KH-{seq:05d}"


def infer_province_id_from_address(
    address: str,
    province_lookup: dict[str, int],
) -> str:
    normalized_address = normalize_vietnamese_text(address)

    if not normalized_address:
        return ""

    for normalized_province_name, province_id in province_lookup.items():
        if normalized_province_name and normalized_province_name in normalized_address:
            return str(province_id)

    aliases = {
        "ha noi": ["tp ha noi", "hanoi"],
        "ho chi minh": ["tp ho chi minh", "hcm", "tphcm", "sai gon"],
        "da nang": ["tp da nang"],
        "hai phong": ["tp hai phong"],
        "can tho": ["tp can tho"],
    }

    for province_name, alias_list in aliases.items():
        province_id = province_lookup.get(province_name)

        if not province_id:
            continue

        if any(alias in normalized_address for alias in alias_list):
            return str(province_id)

    return ""


# ============================================================
# EMAIL LOG
# ============================================================

def build_email_log_row(
    email_data: dict,
    attachment_name: str = "",
    processing_status: str = STATUS_PROCESSING,
    processing_reason: str = REASON_PROCESSING,
) -> dict:
    current_time = now_iso()

    return {
        "message_id": clean_text(email_data.get("message_id", "")),
        "from_address": clean_text(email_data.get("from_address", "")),
        "received_at": clean_text(email_data.get("received_at", "")),
        "attachment_name": attachment_name,
        "processing_status": processing_status,
        "processing_reason": processing_reason,
        "processed_at": current_time,
        "updated_at": current_time,
    }


def update_email_log_row(
    row: dict,
    attachment_name: str | None = None,
    processing_status: str | None = None,
    processing_reason: str | None = None,
) -> dict:
    if attachment_name is not None:
        row["attachment_name"] = attachment_name

    if processing_status is not None:
        row["processing_status"] = processing_status

    if processing_reason is not None:
        row["processing_reason"] = processing_reason

    row["updated_at"] = now_iso()

    return row


def upsert_email_log_row(
    email_log_map: dict[str, dict],
    row: dict,
    fallback_key: str,
) -> None:
    """
    Nếu message_id giống nhau trong batch thì update dòng cũ.
    Nếu message_id rỗng thì dùng fallback_key theo tên file.
    """

    message_id = clean_text(row.get("message_id"))
    key = message_id if message_id else f"__NO_MESSAGE_ID__::{fallback_key}"

    existing = email_log_map.get(key)

    if existing:
        row["processed_at"] = existing.get("processed_at") or row.get("processed_at") or now_iso()
        row["updated_at"] = now_iso()

    email_log_map[key] = row


# ============================================================
# ORDER HEADER / CUSTOMER PARSING
# ============================================================

def normalize_so_number(value: str | None) -> str:
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


def strip_customer_name(value: str) -> str:
    value = clean_text(value)

    value = re.sub(
        r"^(Đại\s*lý|Dai\s*ly|Tên\s*đại\s*lý|Ten\s*dai\s*ly|Khách\s*hàng|Khach\s*hang|Tên|Ten|Đơn\s*vị|Don\s*vi)\s*[:\-]?\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\s+MST\s*[:\-]?\s*\d{8,15}\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\s+(Địa\s*chỉ|Dia\s*chi|Liên\s*hệ|Lien\s*he)\s*[:\-].*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    return clean_text(value)


def extract_first_labeled_value(
    text: str,
    labels: list[str],
    stop_labels: list[str],
) -> str:
    text = clean_multiline_text(text)

    label_pattern = "|".join(labels)
    stop_pattern = "|".join(stop_labels)

    pattern = (
        rf"(?:^|\n)\s*(?:{label_pattern})\s*[:\-]?\s*"
        rf"(.*?)(?=\n\s*(?:{stop_pattern})\s*[:\-]?|\n\s*$|$)"
    )

    match = re.search(
        pattern,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return ""

    return clean_text(match.group(1))


def extract_customer_from_email_body(email_body: str) -> dict:
    body = clean_multiline_text(email_body)

    result = {
        "customer_name_raw": "",
        "tax_code": extract_tax_code(body),
        "address_raw": "",
    }

    customer_labels = [
        r"Đại\s*lý",
        r"Dai\s*ly",
        r"Tên\s*đại\s*lý",
        r"Ten\s*dai\s*ly",
        r"Khách\s*hàng",
        r"Khach\s*hang",
        r"Đơn\s*vị",
        r"Don\s*vi",
        r"Tên",
        r"Ten",
    ]

    address_labels = [
        r"Địa\s*chỉ",
        r"Dia\s*chi",
    ]

    contact_labels = [
        r"Liên\s*hệ",
        r"Lien\s*he",
        r"SĐT",
        r"SDT",
        r"Điện\s*thoại",
        r"Dien\s*thoai",
    ]

    other_stop_labels = [
        r"MST",
        r"File\s*PDF",
        r"Tổng",
        r"Tong",
        r"Mong",
        r"Kính",
        r"Kinh",
    ]

    raw_name = extract_first_labeled_value(
        body,
        customer_labels,
        address_labels + contact_labels + other_stop_labels,
    )

    raw_address = extract_first_labeled_value(
        body,
        address_labels,
        contact_labels + other_stop_labels,
    )

    candidates = []

    if raw_name:
        candidates.append(raw_name)

    for line in body.splitlines():
        line = clean_text(line)

        if re.search(
            r"(CÔNG\s*TY|CONG\s*TY|CỬA\s*HÀNG|CUA\s*HANG|HỘ\s*KINH\s*DOANH|HO\s*KINH\s*DOANH|TNHH|CP|CỔ\s*PHẦN|CO\s*PHAN)",
            line,
            flags=re.IGNORECASE,
        ):
            candidates.append(line)

    cleaned_candidates = []

    for candidate in candidates:
        candidate = strip_customer_name(candidate)

        if candidate and not looks_like_bad_customer_name(candidate):
            cleaned_candidates.append(candidate)

    if cleaned_candidates:
        result["customer_name_raw"] = sorted(cleaned_candidates, key=len)[0]

    result["address_raw"] = clean_text(raw_address)

    return result


def extract_order_header(
    email_data: dict,
    pdf_text: str,
    attachment_name: str = "",
) -> dict:
    source_for_order = "\n".join(
        [
            email_data.get("subject", ""),
            email_data.get("body", ""),
            pdf_text,
            email_data.get("source_email_file", ""),
            attachment_name,
        ]
    )

    email_customer = extract_customer_from_email_body(email_data.get("body", ""))
    so_number = extract_so_number(source_for_order)

    return {
        "so_number": so_number,
        "invoice_symbol": infer_invoice_symbol(so_number),
        "invoice_number": "",
        "order_date": email_data.get("email_date", ""),
        "tax_code": email_customer["tax_code"],
        "customer_name_raw": email_customer["customer_name_raw"],
        "address_raw": email_customer["address_raw"],
    }


# ============================================================
# ORDER LINE PARSING
# ============================================================

PRODUCT_UNITS = [
    "Chiếc",
    "Chiec",
    "Cái",
    "Cai",
    "Bộ",
    "Bo",
    "Cặp",
    "Cap",
    "Thùng",
    "Thung",
    "Hộp",
    "Hop",
    "Cây",
    "Cay",
    "Ngày",
    "Ngay",
]


def split_product_line_rest(rest: str) -> tuple[str, str, str, str, str]:
    unit_pattern = "|".join(re.escape(unit) for unit in PRODUCT_UNITS)

    pattern = (
        rf"^.+?\s+({unit_pattern})\s+"
        rf"(\d+(?:[,.]\d+)*)\s+"
        rf"(\d+(?:[,.]\d+)*)\s+"
        rf"(\d+(?:[,.]\d+)*)$"
    )

    match = re.match(pattern, rest, flags=re.IGNORECASE)

    if match:
        return (
            clean_text(match.group(1)),
            match.group(2),
            match.group(3),
            match.group(4),
            "",
        )

    numbers = re.findall(r"\d+(?:[,.]\d+)*", rest)

    if len(numbers) < 3:
        return "", "", "", "", "Không đủ 3 số cuối để lấy quantity, unit_price, line_total"

    return "", numbers[-3], numbers[-2], numbers[-1], ""


def extract_order_lines(pdf_text: str) -> list[dict]:
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

        unit, quantity_text, unit_price_text, line_total_text, split_error = split_product_line_rest(rest)

        if split_error:
            rows.append(
                {
                    "stt": stt,
                    "product_code": product_code,
                    "unit": unit,
                    "quantity": None,
                    "unit_price": None,
                    "line_total": None,
                    "raw_line": line,
                    "parse_error": split_error,
                    "warning": "",
                }
            )
            continue

        try:
            quantity = parse_quantity(quantity_text)
            unit_price = parse_money(unit_price_text)
            line_total = parse_money(line_total_text)

        except Exception as e:
            rows.append(
                {
                    "stt": stt,
                    "product_code": product_code,
                    "unit": unit,
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
            warning = f"line_total lệch calculated_total: pdf={line_total}, calculated={calculated_total}"

        rows.append(
            {
                "stt": stt,
                "product_code": product_code,
                "unit": unit,
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
# VALIDATION
# ============================================================

def validate_header_required(header: dict) -> list[str]:
    errors = []

    if not header["so_number"]:
        errors.append("Không trích xuất được so_number")

    if not header["order_date"]:
        errors.append("Không lấy được order_date từ email header Date")

    if not header["tax_code"]:
        errors.append("Không trích xuất được MST")

    if not header["customer_name_raw"]:
        errors.append("Không trích xuất được customer_name từ email body")

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
        errors.append(f"product_code không tồn tại trong master, không ghi order_line: {product_code}")

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
# CUSTOMER STAGING
# ============================================================

def build_new_customer_row(
    header: dict,
    email_data: dict,
    customer_lookup: dict[str, str],
    staged_customer_tax_codes: set[str],
    province_lookup: dict[str, int],
    next_customer_seq: dict,
) -> tuple[str, dict | None]:
    tax_code = clean_text(header.get("tax_code"))

    if not tax_code:
        return "", None

    existing_customer_code = customer_lookup.get(tax_code)

    if existing_customer_code:
        return existing_customer_code, None

    customer_code = generate_customer_code(next_customer_seq["value"])
    next_customer_seq["value"] += 1

    customer_lookup[tax_code] = customer_code

    customer_name = clean_text(header.get("customer_name_raw"))
    address = clean_text(header.get("address_raw"))
    province_id = infer_province_id_from_address(address, province_lookup)
    created_at = now_iso()

    if tax_code in staged_customer_tax_codes:
        return customer_code, None

    staged_customer_tax_codes.add(tax_code)

    staging_customer_row = {
        "customer_code": customer_code,
        "customer_name": customer_name,
        "tax_code": tax_code,
        "address": address,
        "province_id": province_id,
        "customer_tier": "STANDARD",
        "is_active": "true",
        "source_email_file": clean_text(email_data.get("source_email_file", "")),
        "source_message_id": clean_text(email_data.get("message_id", "")),
        "source_so_number": clean_text(header.get("so_number", "")),
        "created_at": created_at,
        "updated_at": created_at,
    }

    return customer_code, staging_customer_row


# ============================================================
# PROCESS ONE EXTRACTED EMAIL
# ============================================================

def process_email_to_staging_rows(
    email_data: dict,
    customer_lookup: dict[str, str],
    product_codes: set[str],
    province_lookup: dict[str, int],
    staged_customer_tax_codes: set[str],
    next_customer_seq: dict,
) -> tuple[dict | None, list[dict], list[dict], dict, list[dict]]:
    source_email_file = email_data.get("source_email_file", "")

    fail_rows = []
    staging_customer_rows = []

    attachments = email_data.get("attachments", [])
    email_log_row = build_email_log_row(email_data)

    if not attachments:
        fail_rows.append(
            {
                "record_type": "file",
                "source_email_file": source_email_file,
                "so_number": "",
                "stt": "",
                "product_code": "",
                "error": "Không tìm thấy PDF đính kèm",
                "raw_line": "",
            }
        )

        update_email_log_row(
            email_log_row,
            attachment_name="",
            processing_status=STATUS_FAILED,
            processing_reason=REASON_MISSING_PDF,
        )

        return None, [], fail_rows, email_log_row, []

    first_pdf = attachments[0]
    attachment_name = first_pdf.get("filename", "")
    pdf_text = first_pdf.get("text", "")

    header = extract_order_header(
        email_data=email_data,
        pdf_text=pdf_text,
        attachment_name=attachment_name,
    )

    parsed_lines = extract_order_lines(pdf_text)
    header_errors = validate_header_required(header)

    if header_errors:
        header_error_text = " | ".join(header_errors)

        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": source_email_file,
                "so_number": header.get("so_number", ""),
                "stt": "",
                "product_code": "",
                "error": header_error_text,
                "raw_line": "",
            }
        )

        for line in parsed_lines:
            fail_rows.append(
                {
                    "record_type": "order_line_blocked_by_sales_order_error",
                    "source_email_file": source_email_file,
                    "so_number": header.get("so_number", ""),
                    "stt": line.get("stt", ""),
                    "product_code": line.get("product_code", ""),
                    "error": f"Sales_order lỗi nên không ghi sales_order/order_line: {header_error_text}",
                    "raw_line": line.get("raw_line", ""),
                }
            )

        update_email_log_row(
            email_log_row,
            attachment_name=attachment_name,
            processing_status=STATUS_FAILED,
            processing_reason=REASON_HEADER_ERROR,
        )

        return None, [], fail_rows, email_log_row, []

    if not parsed_lines:
        fail_rows.append(
            {
                "record_type": "order_line",
                "source_email_file": source_email_file,
                "so_number": header["so_number"],
                "stt": "",
                "product_code": "",
                "error": "Không trích xuất được dòng hàng nào từ PDF nên không ghi sales_order/order_line",
                "raw_line": "\n".join(pdf_text.splitlines()[:20]),
            }
        )

        update_email_log_row(
            email_log_row,
            attachment_name=attachment_name,
            processing_status=STATUS_FAILED,
            processing_reason=REASON_NO_LINES,
        )

        return None, [], fail_rows, email_log_row, []

    valid_order_line_rows = []

    for line in parsed_lines:
        line_errors = validate_order_line(line, product_codes)

        if line_errors:
            fail_rows.append(
                {
                    "record_type": "order_line",
                    "source_email_file": source_email_file,
                    "so_number": header["so_number"],
                    "stt": line.get("stt", ""),
                    "product_code": line.get("product_code", ""),
                    "error": " | ".join(line_errors),
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

    if not valid_order_line_rows:
        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": source_email_file,
                "so_number": header["so_number"],
                "stt": "",
                "product_code": "",
                "error": "Không còn order_line hợp lệ sau khi lọc product_code không có trong master nên không ghi sales_order",
                "raw_line": "",
            }
        )

        update_email_log_row(
            email_log_row,
            attachment_name=attachment_name,
            processing_status=STATUS_FAILED,
            processing_reason=REASON_NO_VALID_LINES,
        )

        return None, [], fail_rows, email_log_row, []

    customer_code, staging_customer_row = build_new_customer_row(
        header=header,
        email_data=email_data,
        customer_lookup=customer_lookup,
        staged_customer_tax_codes=staged_customer_tax_codes,
        province_lookup=province_lookup,
        next_customer_seq=next_customer_seq,
    )

    if not customer_code:
        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": source_email_file,
                "so_number": header.get("so_number", ""),
                "stt": "",
                "product_code": "",
                "error": "Không tạo/lấy được customer_code",
                "raw_line": "",
            }
        )

        update_email_log_row(
            email_log_row,
            attachment_name=attachment_name,
            processing_status=STATUS_FAILED,
            processing_reason=REASON_CUSTOMER_ERROR,
        )

        return None, [], fail_rows, email_log_row, []

    if staging_customer_row:
        staging_customer_rows.append(staging_customer_row)

    sales_order_row = {
        "so_number": header["so_number"],
        "invoice_symbol": header["invoice_symbol"],
        "invoice_number": header["invoice_number"],
        "order_date": header["order_date"],
        "customer_code": customer_code,
    }

    has_new_customer = len(staging_customer_rows) > 0
    has_line_error = len(fail_rows) > 0

    if has_new_customer and has_line_error:
        processing_status = STATUS_NEEDS_REVIEW
        processing_reason = REASON_NEW_CUSTOMER_LINE_ERROR
    elif has_new_customer:
        processing_status = STATUS_NEEDS_REVIEW
        processing_reason = REASON_NEW_CUSTOMER
    elif has_line_error:
        processing_status = STATUS_NEEDS_REVIEW
        processing_reason = REASON_LINE_ERROR
    else:
        processing_status = STATUS_SUCCESS
        processing_reason = REASON_SUCCESS

    update_email_log_row(
        email_log_row,
        attachment_name=attachment_name,
        processing_status=processing_status,
        processing_reason=processing_reason,
    )

    return (
        sales_order_row,
        valid_order_line_rows,
        fail_rows,
        email_log_row,
        staging_customer_rows,
    )


# ============================================================
# FAIL SUMMARY
# ============================================================

def normalize_error(error: str) -> str:
    error = clean_text(error)

    if not error:
        return ""

    rules = [
        ("Trùng so_number trong batch", "Trùng so_number trong batch"),
        ("Không tìm thấy PDF đính kèm", "Không tìm thấy PDF đính kèm"),
        ("Không trích xuất được so_number", "Không trích xuất được so_number"),
        ("Không lấy được order_date từ email header Date", "Không lấy được order_date từ email header Date"),
        ("Không trích xuất được MST", "Không trích xuất được MST"),
        ("Không trích xuất được customer_name", "Không trích xuất được customer_name từ email body"),
        ("Không tạo/lấy được customer_code", "Không tạo/lấy được customer_code"),
        ("product_code không tồn tại trong master", "product_code không tồn tại trong master"),
        ("Không đủ 3 số cuối", "Không đủ 3 số cuối để lấy quantity, unit_price, line_total"),
        ("Lỗi parse số:", "Lỗi parse số"),
        ("Thiếu product_code", "Thiếu product_code"),
        ("Thiếu quantity", "Thiếu quantity"),
        ("quantity <= 0", "quantity <= 0"),
        ("Thiếu unit_price", "Thiếu unit_price"),
        ("unit_price < 0", "unit_price < 0"),
        ("Thiếu line_total", "Thiếu line_total"),
        ("line_total <= 0", "line_total <= 0"),
        ("line_total lệch calculated_total", "line_total lệch calculated_total"),
        ("Không trích xuất được dòng hàng nào từ PDF", "Không trích xuất được dòng hàng nào từ PDF"),
        ("Không còn order_line hợp lệ", "Không còn order_line hợp lệ"),
        ("Sales_order lỗi", "Sales_order lỗi"),
    ]

    for keyword, group_name in rules:
        if keyword in error:
            return group_name

    return error


def summarize_fail_rows(fail_rows: list[dict]) -> list[dict]:
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


# ============================================================
# CSV WRITER
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
                {
                    key: none_to_empty(row.get(key))
                    for key in fieldnames
                }
            )


# ============================================================
# MAIN PIPELINE
# ============================================================

def extract_emails_to_staging(
    input_dir: str | Path = DEFAULT_INPUT_DIR,
    output_dir: str | Path = DEFAULT_STAGING_DIR,
    quality_check_dir: str | Path = DEFAULT_QUALITY_CHECK_DIR,
    limit: int | None = None,
) -> dict:
    input_dir = resolve_project_path(input_dir)
    output_dir = ensure_dir(output_dir)
    quality_check_dir = ensure_dir(quality_check_dir)

    logger.info("=" * 80)
    logger.info("EXTRACT TO STAGING STARTED")
    logger.info("=" * 80)
    logger.info("Input dir         : %s", input_dir)
    logger.info("Staging dir       : %s", output_dir)
    logger.info("Quality check dir : %s", quality_check_dir)
    logger.info("DB schema         : %s", DB_SCHEMA)
    logger.info("=" * 80)

    customer_lookup = load_customer_lookup_from_db()
    product_codes = load_product_codes_from_db()
    province_lookup = load_province_lookup_from_db()

    next_customer_seq = {
        "value": load_next_customer_sequence_from_db()
    }

    staged_customer_tax_codes = set()

    email_log_map = {}
    sales_order_rows = []
    order_line_rows = []
    fail_rows = []
    staging_customer_rows = []

    eml_files = sorted(input_dir.glob("*.eml"))

    if limit is not None and limit > 0:
        eml_files = eml_files[:limit]

    logger.info("FOUND EMAIL FILES | Count: %s", len(eml_files))

    seen_so_numbers = set()

    for idx, eml_path in enumerate(eml_files, start=1):
        logger.info("-" * 80)
        logger.info("PROCESSING %s/%s | %s", idx, len(eml_files), eml_path.name)

        try:
            email_data = extract_email(eml_path)

            (
                sales_order_row,
                valid_line_rows,
                file_fail_rows,
                email_log_row,
                file_staging_customers,
            ) = process_email_to_staging_rows(
                email_data=email_data,
                customer_lookup=customer_lookup,
                product_codes=product_codes,
                province_lookup=province_lookup,
                staged_customer_tax_codes=staged_customer_tax_codes,
                next_customer_seq=next_customer_seq,
            )

            fail_rows.extend(file_fail_rows)
            staging_customer_rows.extend(file_staging_customers)

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

                    if email_log_row:
                        update_email_log_row(
                            email_log_row,
                            processing_status=STATUS_FAILED,
                            processing_reason=REASON_DUPLICATE_SO,
                        )

                else:
                    if so_number:
                        seen_so_numbers.add(so_number)

                    sales_order_rows.append(sales_order_row)

            if email_log_row:
                upsert_email_log_row(
                    email_log_map=email_log_map,
                    row=email_log_row,
                    fallback_key=eml_path.name,
                )

            order_line_rows.extend(valid_line_rows)

            current_status = (
                email_log_row.get("processing_status", "")
                if email_log_row
                else STATUS_FAILED
            )

            current_reason = (
                email_log_row.get("processing_reason", "")
                if email_log_row
                else REASON_EXCEPTION
            )

            logger.info(
                "FILE SUMMARY | File: %s | Status: %s | Reason: %s | Lines: %s | Fail rows: %s",
                eml_path.name,
                current_status,
                current_reason,
                len(valid_line_rows),
                len(file_fail_rows),
            )

        except Exception as e:
            logger.exception("EXCEPTION | File: %s | Error: %s", eml_path.name, e)

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

            current_time = now_iso()

            exception_email_log_row = {
                "message_id": "",
                "from_address": "",
                "received_at": "",
                "attachment_name": "",
                "processing_status": STATUS_FAILED,
                "processing_reason": REASON_EXCEPTION,
                "processed_at": current_time,
                "updated_at": current_time,
            }

            upsert_email_log_row(
                email_log_map=email_log_map,
                row=exception_email_log_row,
                fallback_key=eml_path.name,
            )

    email_log_rows = list(email_log_map.values())
    fail_summary_rows = summarize_fail_rows(fail_rows)

    output_paths = {
        "email_log": output_dir / OUT_EMAIL_LOG,
        "staging_customer": output_dir / OUT_STAGING_CUSTOMER,
        "sales_order": output_dir / OUT_SALES_ORDER,
        "order_line": output_dir / OUT_ORDER_LINE,
        "extract_fail": quality_check_dir / OUT_EXTRACT_FAIL,
        "extract_fail_summary": quality_check_dir / OUT_EXTRACT_FAIL_SUMMARY,
    }

    write_csv(
        output_paths["email_log"],
        [
            "message_id",
            "from_address",
            "received_at",
            "attachment_name",
            "processing_status",
            "processing_reason",
            "processed_at",
            "updated_at",
        ],
        email_log_rows,
    )

    write_csv(
        output_paths["staging_customer"],
        [
            "customer_code",
            "customer_name",
            "tax_code",
            "address",
            "province_id",
            "customer_tier",
            "is_active",
            "source_email_file",
            "source_message_id",
            "source_so_number",
            "created_at",
            "updated_at",
        ],
        staging_customer_rows,
    )

    write_csv(
        output_paths["sales_order"],
        ["so_number", "invoice_symbol", "invoice_number", "order_date", "customer_code"],
        sales_order_rows,
    )

    write_csv(
        output_paths["order_line"],
        ["order_id", "so_number", "product_code", "quantity", "unit_price", "line_total"],
        order_line_rows,
    )

    write_csv(
        output_paths["extract_fail"],
        ["record_type", "source_email_file", "so_number", "stt", "product_code", "error", "raw_line"],
        fail_rows,
    )

    write_csv(
        output_paths["extract_fail_summary"],
        ["group_type", "group_value", "count"],
        fail_summary_rows,
    )

    status_counter = Counter(row.get("processing_status", "") for row in email_log_rows)
    reason_counter = Counter(row.get("processing_reason", "") for row in email_log_rows)

    summary = {
        "email_files": len(eml_files),
        "email_log_rows": len(email_log_rows),
        "sales_order_rows": len(sales_order_rows),
        "order_line_rows": len(order_line_rows),
        "fail_rows": len(fail_rows),
        "fail_summary_rows": len(fail_summary_rows),
        "staging_customer_rows": len(staging_customer_rows),
        "status_counts": dict(status_counter),
        "reason_counts": dict(reason_counter),
        "output_paths": output_paths,
    }

    logger.info("=" * 80)
    logger.info("EXTRACT TO STAGING FINISHED")
    logger.info("Email files           : %s", summary["email_files"])
    logger.info("Email log rows        : %s", summary["email_log_rows"])
    logger.info("Sales orders          : %s", summary["sales_order_rows"])
    logger.info("Order line rows       : %s", summary["order_line_rows"])
    logger.info("Fail rows             : %s", summary["fail_rows"])
    logger.info("Staging customers     : %s", summary["staging_customer_rows"])
    logger.info("Status counts         : %s", summary["status_counts"])
    logger.info("Reason counts         : %s", summary["reason_counts"])
    logger.info("=" * 80)

    return summary


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract TNBIKE emails to staging CSV"
    )

    parser.add_argument(
        "--input-dir",
        default=DEFAULT_INPUT_DIR,
        help="Input folder containing .eml files",
    )

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_STAGING_DIR,
        help="Output staging folder for CSV files prepared to load DB",
    )

    parser.add_argument(
        "--quality-check-dir",
        default=DEFAULT_QUALITY_CHECK_DIR,
        help="Output folder for extract fail and fail summary files",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of .eml files for testing",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="extract_to_staging.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        summary = extract_emails_to_staging(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            quality_check_dir=args.quality_check_dir,
            limit=args.limit,
        )

        print("")
        print("EXTRACT TO STAGING SUCCESS")
        print(f"Email files           : {summary['email_files']}")
        print(f"Email log rows        : {summary['email_log_rows']}")
        print(f"Staging customers     : {summary['staging_customer_rows']}")
        print(f"Sales orders          : {summary['sales_order_rows']}")
        print(f"Order line rows       : {summary['order_line_rows']}")
        print(f"Fail rows             : {summary['fail_rows']}")
        print(f"Fail summary rows     : {summary['fail_summary_rows']}")
        print(f"Staging dir           : {resolve_project_path(args.output_dir)}")
        print(f"Quality check dir     : {resolve_project_path(args.quality_check_dir)}")
        print(f"Status counts         : {summary['status_counts']}")
        print(f"Reason counts         : {summary['reason_counts']}")

    except Exception as e:
        logger.exception("EXTRACT TO STAGING FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()