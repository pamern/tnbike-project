from pathlib import Path
import csv
import os
import re
import tempfile
import unicodedata
import logging
from datetime import datetime
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


# ============================================================
# Terminal logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("tnbike_email_parser")


RAW_EMAIL_DIR = Path(r"data\incoming\eml")

OUT_DIR = Path("data/staging")
OUT_EMAIL_LOG = OUT_DIR / "staging_email_log.csv"
OUT_SALES_ORDER = OUT_DIR / "staging_sales_order.csv"
OUT_ORDER_LINE = OUT_DIR / "staging_order_line.csv"
OUT_FAILED = OUT_DIR / "staging_fail.csv"
OUT_FAILED_SUMMARY = OUT_DIR / "staging_fail_summary.csv"

# Chỉ staging customer mới. Không staging product vì product_name trong PDF lỗi font.
OUT_STAGING_CUSTOMER = OUT_DIR / "staging_customer.csv"
OUT_STAGING_CUSTOMER_LOG = OUT_DIR / "staging_customer_log.csv"


# ============================================================
# Standard processing_status values for dashboard
# ============================================================

STATUS_PROCESSING = "PROCESSING"      # Đang xử lý
STATUS_SUCCESS = "SUCCESS"            # Đã xử lý thành công
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"  # Chờ kiểm tra
STATUS_FAILED = "FAILED"              # Lỗi xử lý

# Chỉ dùng cho staging_customer_log, không dùng cho email_log.processing_status
STATUS_NEW_CUSTOMER = "NEW_CUSTOMER"


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

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_multiline_text(value: str | None) -> str:
    if not value:
        return ""
    lines = [clean_text(line) for line in str(value).splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def parse_money(value: str | None) -> Decimal:
    """
    1.898.148 -> Decimal("1898148")
    """
    if not value:
        return Decimal("0")

    cleaned = (
        str(value).strip()
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

    return Decimal(str(value).strip().replace(",", "."))


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
    Product mới không được tự động thêm vào master vì product_name trong PDF không đáng tin cậy.
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


def load_next_customer_sequence_from_db() -> int:
    """
    Lấy số thứ tự customer_code lớn nhất hiện có trong master customer.
    Ví dụ đang có KH-00702 -> trả về 703.
    """

    sql = """
        SELECT customer_code
        FROM tnbike.customer
        WHERE customer_code IS NOT NULL;
    """

    max_no = 0

    with psycopg2.connect(**DB_CONFIG) as conn:
        conn.set_session(readonly=True, autocommit=True)

        with conn.cursor() as cur:
            cur.execute(sql)

            for (customer_code,) in cur.fetchall():
                match = re.search(r"KH-(\d+)", clean_text(customer_code), flags=re.IGNORECASE)

                if match:
                    max_no = max(max_no, int(match.group(1)))

    return max_no + 1


def load_province_lookup_from_db() -> dict[str, int]:
    """
    READ ONLY:
    normalized province_name -> province_id.
    """

    sql = """
        SELECT province_id, province_name
        FROM tnbike.province;
    """

    lookup = {}

    with psycopg2.connect(**DB_CONFIG) as conn:
        conn.set_session(readonly=True, autocommit=True)

        with conn.cursor() as cur:
            cur.execute(sql)

            for province_id, province_name in cur.fetchall():
                normalized_name = normalize_vietnamese_text(province_name)

                if normalized_name:
                    lookup[normalized_name] = province_id

    return lookup


def generate_customer_code(seq: int) -> str:
    return f"KH-{seq:05d}"


def infer_province_id_from_address(address: str, province_lookup: dict[str, int]) -> str:
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
    processing_status: str = STATUS_PROCESSING
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
# PDF extraction: only pdfplumber
# ============================================================

def extract_pdf_text(pdf_path: Path) -> tuple[str, str]:
    chunks = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            chunks.append(text)

    return "\n".join(chunks), "pdfplumber"


# ============================================================
# Header/customer parsing
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
    match = re.search(r"\bMST\s*[:\-]?\s*(\d{8,15})\b", source, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def strip_customer_name(value: str) -> str:
    value = clean_text(value)

    value = re.sub(
        r"^(Đại\s*lý|Dai\s*ly|Tên\s*đại\s*lý|Ten\s*dai\s*ly|Khách\s*hàng|Khach\s*hang|Tên|Ten|Đơn\s*vị|Don\s*vi)\s*[:\-]?\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(r"\s+MST\s*[:\-]?\s*\d{8,15}\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(Địa\s*chỉ|Dia\s*chi|Liên\s*hệ|Lien\s*he)\s*[:\-].*$", "", value, flags=re.IGNORECASE)

    return clean_text(value)


def extract_first_labeled_value(text: str, labels: list[str], stop_labels: list[str]) -> str:
    """
    Extract linh hoạt dạng:
    - Label: value
    - Label : value xuống dòng tiếp theo
    - Value có thể bị wrap nhiều dòng
    Dừng khi gặp label khác.
    """

    text = clean_multiline_text(text)
    label_pattern = "|".join(labels)
    stop_pattern = "|".join(stop_labels)

    pattern = rf"(?:^|\n)\s*(?:{label_pattern})\s*[:\-]?\s*(.*?)(?=\n\s*(?:{stop_pattern})\s*[:\-]?|\n\s*$|$)"

    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)

    if not match:
        return ""

    return clean_text(match.group(1))


def extract_customer_from_email_body(email_body: str) -> dict:
    """
    Parse customer từ email body, không lấy từ PDF để tránh lỗi font.
    Mục tiêu: linh hoạt nhưng nhanh, không AI/OCR.
    """

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

    address_labels = [r"Địa\s*chỉ", r"Dia\s*chi"]
    contact_labels = [r"Liên\s*hệ", r"Lien\s*he", r"SĐT", r"SDT", r"Điện\s*thoại", r"Dien\s*thoai"]
    other_stop_labels = [r"MST", r"File\s*PDF", r"Tổng", r"Tong", r"Mong", r"Kính", r"Kinh"]

    stop_for_name = address_labels + contact_labels + other_stop_labels
    stop_for_address = contact_labels + other_stop_labels

    raw_name = extract_first_labeled_value(body, customer_labels, stop_for_name)
    raw_address = extract_first_labeled_value(body, address_labels, stop_for_address)

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
    email_subject: str,
    email_body: str,
    pdf_text: str,
    email_header_date: str
) -> dict:
    source_for_order = "\n".join([email_subject, email_body, pdf_text])
    email_customer = extract_customer_from_email_body(email_body)

    so_number = extract_so_number(source_for_order)

    return {
        "so_number": so_number,
        "invoice_symbol": infer_invoice_symbol(so_number),
        "invoice_number": "",
        "order_date": email_header_date,
        "tax_code": email_customer["tax_code"],
        "customer_name_raw": email_customer["customer_name_raw"],
        "address_raw": email_customer["address_raw"],
    }


# ============================================================
# Order line parsing
# ============================================================

PRODUCT_UNITS = [
    "Chiếc", "Chiec", "Cái", "Cai", "Bộ", "Bo", "Cặp", "Cap", "Thùng", "Thung",
    "Hộp", "Hop", "Cây", "Cay", "Ngày", "Ngay"
]


def split_product_line_rest(rest: str) -> tuple[str, str, str, str, str]:
    """
    Output:
    - unit
    - quantity_text
    - unit_price_text
    - line_total_text
    - parse_error

    Không dùng product_name để tạo master vì PDF lỗi font.
    Chỉ cần product_code, quantity, unit_price, line_total cho giao dịch.
    """

    unit_pattern = "|".join(re.escape(unit) for unit in PRODUCT_UNITS)
    pattern = rf"^.+?\s+({unit_pattern})\s+(\d+(?:[,.]\d+)*)\s+(\d+(?:[,.]\d+)*)\s+(\d+(?:[,.]\d+)*)$"
    match = re.match(pattern, rest, flags=re.IGNORECASE)

    if match:
        return clean_text(match.group(1)), match.group(2), match.group(3), match.group(4), ""

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

        calculated_total = (quantity * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
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
# Validation
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
# Staging customer builder
# ============================================================

def build_new_customer_rows(
    header: dict,
    eml_path: Path,
    customer_lookup: dict[str, str],
    staged_customer_tax_codes: set[str],
    province_lookup: dict[str, int],
    next_customer_seq: dict,
) -> tuple[str, dict | None, dict | None]:
    tax_code = clean_text(header.get("tax_code"))

    if not tax_code:
        return "", None, None

    existing_customer_code = customer_lookup.get(tax_code)

    if existing_customer_code:
        return existing_customer_code, None, None

    customer_code = generate_customer_code(next_customer_seq["value"])
    next_customer_seq["value"] += 1
    customer_lookup[tax_code] = customer_code

    customer_name = clean_text(header.get("customer_name_raw"))
    address = clean_text(header.get("address_raw"))
    province_id = infer_province_id_from_address(address, province_lookup)
    created_at = now_iso()

    staging_customer_row = None
    staging_customer_log_row = None

    if tax_code not in staged_customer_tax_codes:
        staged_customer_tax_codes.add(tax_code)

        staging_customer_row = {
            "customer_code": customer_code,
            "customer_name": customer_name,
            "tax_code": tax_code,
            "address": address,
            "province_id": province_id,
            "customer_tier": "STANDARD",
            "is_active": "true",
            "created_at": created_at,
            "updated_at": created_at,
        }

        staging_customer_log_row = {
            "customer_code": customer_code,
            "tax_code": tax_code,
            "so_number": header.get("so_number", ""),
            "source_email_file": eml_path.name,
            "status": STATUS_NEW_CUSTOMER,
            "created_at": created_at,
        }

    return customer_code, staging_customer_row, staging_customer_log_row


# ============================================================
# Parse one email
# ============================================================

def parse_email_file(
    eml_path: Path,
    customer_lookup: dict[str, str],
    product_codes: set[str],
    province_lookup: dict[str, int],
    staged_customer_tax_codes: set[str],
    next_customer_seq: dict,
) -> tuple[dict | None, list[dict], list[dict], dict, list[dict], list[dict]]:
    """
    Return:
    - sales_order_row hoặc None
    - valid_order_line_rows
    - fail_rows
    - email_log_row
    - staging_customer_rows
    - staging_customer_log_rows
    """

    fail_rows = []
    staging_customer_rows = []
    staging_customer_log_rows = []

    msg = parse_email(eml_path)
    logger.info("START | File: %s", eml_path.name)

    email_subject = clean_text(msg.get("Subject", ""))
    email_body = get_email_body(msg)
    email_header_date = get_email_header_date(msg)

    logger.info(
        "PARSED EMAIL | File: %s | Subject: %s | Date: %s",
        eml_path.name,
        email_subject,
        email_header_date,
    )

    attachment_name = ""

    email_log_row = build_email_log_row(
        msg=msg,
        attachment_name=attachment_name,
        processing_status=STATUS_PROCESSING,
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
                    "processing_status": STATUS_FAILED,
                }
            )

            logger.error(
                "FAILED | File: %s | Reason: Không tìm thấy PDF đính kèm",
                eml_path.name,
            )

            return None, [], fail_rows, email_log_row, [], []

        attachment_name = pdf_path.name
        pdf_text, extraction_method = extract_pdf_text(pdf_path)

    logger.info(
        "PDF EXTRACTED | File: %s | Attachment: %s | Method: %s | Text length: %s",
        eml_path.name,
        attachment_name,
        extraction_method,
        len(pdf_text),
    )

    header = extract_order_header(
        email_subject=email_subject,
        email_body=email_body,
        pdf_text=pdf_text,
        email_header_date=email_header_date,
    )

    logger.info(
        "HEADER | File: %s | SO: %s | Date: %s | Tax code: %s | Customer raw: %s",
        eml_path.name,
        header.get("so_number", ""),
        header.get("order_date", ""),
        header.get("tax_code", ""),
        header.get("customer_name_raw", ""),
    )

    parsed_lines = extract_order_lines(pdf_text)

    logger.info(
        "LINES PARSED | File: %s | SO: %s | Parsed lines: %s",
        eml_path.name,
        header.get("so_number", ""),
        len(parsed_lines),
    )

    header_errors = validate_header_required(header)

    if header_errors:
        header_error_text = " | ".join(header_errors)

        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": eml_path.name,
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
                    "source_email_file": eml_path.name,
                    "so_number": header.get("so_number", ""),
                    "stt": line.get("stt", ""),
                    "product_code": line.get("product_code", ""),
                    "error": f"Sales_order lỗi nên không ghi sales_order/order_line: {header_error_text}",
                    "raw_line": line.get("raw_line", ""),
                }
            )

        email_log_row.update(
            {
                "attachment_name": attachment_name,
                "processing_status": STATUS_FAILED,
            }
        )

        logger.error(
            "FAILED | File: %s | SO: %s | Header errors: %s",
            eml_path.name,
            header.get("so_number", ""),
            header_error_text,
        )

        return None, [], fail_rows, email_log_row, [], []

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
                "processing_status": STATUS_FAILED,
            }
        )

        logger.error(
            "FAILED | File: %s | SO: %s | Reason: Không trích xuất được dòng hàng nào",
            eml_path.name,
            header.get("so_number", ""),
        )

        return None, [], fail_rows, email_log_row, [], []

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

    logger.info(
        "LINES VALIDATED | File: %s | SO: %s | Valid lines: %s | Failed rows so far: %s",
        eml_path.name,
        header.get("so_number", ""),
        len(valid_order_line_rows),
        len(fail_rows),
    )

    if not valid_order_line_rows:
        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": eml_path.name,
                "so_number": header["so_number"],
                "stt": "",
                "product_code": "",
                "error": "Không còn order_line hợp lệ sau khi lọc product_code không có trong master nên không ghi sales_order",
                "raw_line": "",
            }
        )

        email_log_row.update(
            {
                "attachment_name": attachment_name,
                "processing_status": STATUS_FAILED,
            }
        )

        logger.error(
            "FAILED | File: %s | SO: %s | Reason: Không còn order_line hợp lệ",
            eml_path.name,
            header.get("so_number", ""),
        )

        return None, [], fail_rows, email_log_row, [], []

    customer_code, staging_customer_row, staging_customer_log_row = build_new_customer_rows(
        header=header,
        eml_path=eml_path,
        customer_lookup=customer_lookup,
        staged_customer_tax_codes=staged_customer_tax_codes,
        province_lookup=province_lookup,
        next_customer_seq=next_customer_seq,
    )

    if not customer_code:
        fail_rows.append(
            {
                "record_type": "sales_order",
                "source_email_file": eml_path.name,
                "so_number": header.get("so_number", ""),
                "stt": "",
                "product_code": "",
                "error": "Không tạo/lấy được customer_code",
                "raw_line": "",
            }
        )

        email_log_row.update(
            {
                "attachment_name": attachment_name,
                "processing_status": STATUS_FAILED,
            }
        )

        logger.error(
            "FAILED | File: %s | SO: %s | Reason: Không tạo/lấy được customer_code",
            eml_path.name,
            header.get("so_number", ""),
        )

        return None, [], fail_rows, email_log_row, [], []

    if staging_customer_row:
        staging_customer_rows.append(staging_customer_row)

    if staging_customer_log_row:
        staging_customer_log_rows.append(staging_customer_log_row)

    sales_order_row = {
        "so_number": header["so_number"],
        "invoice_symbol": header["invoice_symbol"],
        "invoice_number": header["invoice_number"],
        "order_date": header["order_date"],
        "customer_code": customer_code,
    }

    if staging_customer_rows or fail_rows:
        processing_status = STATUS_NEEDS_REVIEW
    else:
        processing_status = STATUS_SUCCESS

    email_log_row.update(
        {
            "attachment_name": attachment_name,
            "processing_status": processing_status,
        }
    )

    logger.info(
        "DONE | File: %s | SO: %s | Status: %s | Valid lines: %s | Fail rows: %s | New customers: %s",
        eml_path.name,
        header.get("so_number", ""),
        processing_status,
        len(valid_order_line_rows),
        len(fail_rows),
        len(staging_customer_rows),
    )

    return (
        sales_order_row,
        valid_order_line_rows,
        fail_rows,
        email_log_row,
        staging_customer_rows,
        staging_customer_log_rows,
    )


# ============================================================
# Fail summary
# ============================================================

def normalize_error(error: str) -> str:
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

    if "Không trích xuất được customer_name" in error:
        return "Không trích xuất được customer_name từ email body"

    if "Không tạo/lấy được customer_code" in error:
        return "Không tạo/lấy được customer_code"

    if "product_code không tồn tại trong master" in error:
        return "product_code không tồn tại trong master"

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

    if "Không còn order_line hợp lệ" in error:
        return "Không còn order_line hợp lệ"

    if "Sales_order lỗi" in error:
        return "Sales_order lỗi"

    return error


def summarize_fail_rows(fail_rows: list[dict]) -> list[dict]:
    summary_rows = []

    by_record_type = Counter(row.get("record_type", "") for row in fail_rows)
    by_error_group = Counter(normalize_error(row.get("error", "")) for row in fail_rows)

    for record_type, count in by_record_type.most_common():
        summary_rows.append({"group_type": "record_type", "group_value": record_type, "count": count})

    for error_group, count in by_error_group.most_common():
        summary_rows.append({"group_type": "error_group", "group_value": error_group, "count": count})

    return summary_rows


# ============================================================
# CSV writer
# ============================================================

def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            writer.writerow({key: none_to_empty(row.get(key)) for key in fieldnames})


# ============================================================
# Main
# ============================================================

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 80)
    logger.info("START PIPELINE | Email dir: %s | Output dir: %s", RAW_EMAIL_DIR, OUT_DIR)
    logger.info("=" * 80)

    customer_lookup = load_customer_lookup_from_db()
    product_codes = load_product_codes_from_db()
    province_lookup = load_province_lookup_from_db()

    logger.info(
        "DB LOOKUP LOADED | Customers: %s | Products: %s | Provinces: %s",
        len(customer_lookup),
        len(product_codes),
        len(province_lookup),
    )

    next_customer_seq = {"value": load_next_customer_sequence_from_db()}
    staged_customer_tax_codes = set()

    logger.info("NEXT CUSTOMER SEQUENCE | Start from: KH-%05d", next_customer_seq["value"])

    email_log_rows = []
    sales_order_rows = []
    order_line_rows = []
    fail_rows = []
    staging_customer_rows = []
    staging_customer_log_rows = []

    eml_files = sorted(RAW_EMAIL_DIR.glob("*.eml"))

    logger.info("FOUND EMAIL FILES | Count: %s", len(eml_files))

    if not eml_files:
        logger.warning("Không tìm thấy file .eml trong %s", RAW_EMAIL_DIR)
        print(f"[WARN] Không tìm thấy file .eml trong {RAW_EMAIL_DIR}")

    seen_so_numbers = set()

    for idx, eml_path in enumerate(eml_files, start=1):
        logger.info("-" * 80)
        logger.info("PROCESSING %s/%s | %s", idx, len(eml_files), eml_path.name)

        try:
            (
                sales_order_row,
                valid_line_rows,
                file_fail_rows,
                email_log_row,
                file_staging_customers,
                file_staging_customer_logs,
            ) = parse_email_file(
                eml_path=eml_path,
                customer_lookup=customer_lookup,
                product_codes=product_codes,
                province_lookup=province_lookup,
                staged_customer_tax_codes=staged_customer_tax_codes,
                next_customer_seq=next_customer_seq,
            )

            fail_rows.extend(file_fail_rows)
            staging_customer_rows.extend(file_staging_customers)
            staging_customer_log_rows.extend(file_staging_customer_logs)

            if sales_order_row:
                so_number = sales_order_row.get("so_number", "")

                if so_number and so_number in seen_so_numbers:
                    logger.warning(
                        "DUPLICATE SO | File: %s | SO: %s | Mark status FAILED",
                        eml_path.name,
                        so_number,
                    )

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
                        email_log_row["processing_status"] = STATUS_FAILED

                else:
                    if so_number:
                        seen_so_numbers.add(so_number)

                    sales_order_rows.append(sales_order_row)

            if email_log_row:
                email_log_rows.append(email_log_row)

            order_line_rows.extend(valid_line_rows)

            current_status = email_log_row.get("processing_status", "") if email_log_row else STATUS_FAILED

            logger.info(
                "FILE SUMMARY | File: %s | Status: %s | Sales order added: %s | Valid lines added: %s | File fail rows: %s | New customers: %s",
                eml_path.name,
                current_status,
                1 if sales_order_row and current_status != STATUS_FAILED else 0,
                len(valid_line_rows),
                len(file_fail_rows),
                len(file_staging_customers),
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

            email_log_rows.append(
                {
                    "message_id": "",
                    "from_address": "",
                    "received_at": "",
                    "attachment_name": "",
                    "processing_status": STATUS_FAILED,
                }
            )

    fail_summary_rows = summarize_fail_rows(fail_rows)

    logger.info("-" * 80)
    logger.info("WRITING CSV OUTPUTS")

    write_csv(
        OUT_EMAIL_LOG,
        ["message_id", "from_address", "received_at", "attachment_name", "processing_status"],
        email_log_rows,
    )
    logger.info("WROTE | %s | Rows: %s", OUT_EMAIL_LOG, len(email_log_rows))

    write_csv(
        OUT_STAGING_CUSTOMER,
        [
            "customer_code",
            "customer_name",
            "tax_code",
            "address",
            "province_id",
            "customer_tier",
            "is_active",
            "created_at",
            "updated_at",
        ],
        staging_customer_rows,
    )
    logger.info("WROTE | %s | Rows: %s", OUT_STAGING_CUSTOMER, len(staging_customer_rows))

    write_csv(
        OUT_SALES_ORDER,
        ["so_number", "invoice_symbol", "invoice_number", "order_date", "customer_code"],
        sales_order_rows,
    )
    logger.info("WROTE | %s | Rows: %s", OUT_SALES_ORDER, len(sales_order_rows))

    write_csv(
        OUT_ORDER_LINE,
        ["order_id", "so_number", "product_code", "quantity", "unit_price", "line_total"],
        order_line_rows,
    )
    logger.info("WROTE | %s | Rows: %s", OUT_ORDER_LINE, len(order_line_rows))

    write_csv(
        OUT_FAILED,
        ["record_type", "source_email_file", "so_number", "stt", "product_code", "error", "raw_line"],
        fail_rows,
    )
    logger.info("WROTE | %s | Rows: %s", OUT_FAILED, len(fail_rows))

    write_csv(
        OUT_FAILED_SUMMARY,
        ["group_type", "group_value", "count"],
        fail_summary_rows,
    )
    logger.info("WROTE | %s | Rows: %s", OUT_FAILED_SUMMARY, len(fail_summary_rows))

    write_csv(
        OUT_STAGING_CUSTOMER_LOG,
        ["customer_code", "tax_code", "so_number", "source_email_file", "status", "created_at"],
        staging_customer_log_rows,
    )
    logger.info("WROTE | %s | Rows: %s", OUT_STAGING_CUSTOMER_LOG, len(staging_customer_log_rows))

    print(f"Email log            : {len(email_log_rows)} -> {OUT_EMAIL_LOG}")
    print(f"Staging customers    : {len(staging_customer_rows)} -> {OUT_STAGING_CUSTOMER}")
    print(f"Sales orders         : {len(sales_order_rows)} -> {OUT_SALES_ORDER}")
    print(f"Order line rows      : {len(order_line_rows)} -> {OUT_ORDER_LINE}")
    print(f"Fail rows            : {len(fail_rows)} -> {OUT_FAILED}")
    print(f"Fail summary         : {len(fail_summary_rows)} -> {OUT_FAILED_SUMMARY}")
    print(f"Staging customer log : {len(staging_customer_log_rows)} -> {OUT_STAGING_CUSTOMER_LOG}")

    status_counter = Counter(row.get("processing_status", "") for row in email_log_rows)

    logger.info("=" * 80)
    logger.info("PIPELINE FINISHED")
    logger.info("Email log rows         : %s", len(email_log_rows))
    logger.info("Sales orders           : %s", len(sales_order_rows))
    logger.info("Order line rows        : %s", len(order_line_rows))
    logger.info("Fail rows              : %s", len(fail_rows))
    logger.info("Staging customers      : %s", len(staging_customer_rows))
    logger.info("Staging customer logs  : %s", len(staging_customer_log_rows))

    logger.info("PROCESSING STATUS SUMMARY")
    for status, count in status_counter.most_common():
        logger.info("- %s: %s", status, count)

    logger.info("=" * 80)


if __name__ == "__main__":
    main()