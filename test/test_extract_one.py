import json
import re
from pathlib import Path
from email import policy
from email.parser import BytesParser
from email.header import decode_header
from email.utils import parsedate_to_datetime

import pdfplumber


def decode_header_value(value):
    if not value:
        return None

    parts = decode_header(value)
    result = ""

    for part, encoding in parts:
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8", errors="replace")
        else:
            result += part

    return result.strip()


def parse_money(value):
    if not value:
        return None

    cleaned = re.sub(r"[^\d]", "", str(value))
    return int(cleaned) if cleaned else None


def clean_text(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def read_eml(eml_path, output_dir="data/processed/attachments"):
    eml_path = Path(eml_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with eml_path.open("rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    raw_date = msg.get("Date")
    try:
        received_at = parsedate_to_datetime(raw_date).isoformat() if raw_date else None
    except Exception:
        received_at = raw_date

    body_parts = []
    attachments = []

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()

        if disposition == "attachment":
            filename = decode_header_value(part.get_filename())
            payload = part.get_payload(decode=True)

            if payload and (
                content_type == "application/pdf"
                or (filename and filename.lower().endswith(".pdf"))
            ):
                if not filename:
                    filename = eml_path.stem + ".pdf"

                save_path = output_dir / filename
                save_path.write_bytes(payload)

                attachments.append({
                    "filename": filename,
                    "content_type": content_type,
                    "saved_path": str(save_path),
                    "size_bytes": len(payload),
                })

        elif content_type == "text/plain":
            try:
                body_parts.append(part.get_content())
            except Exception:
                payload = part.get_payload(decode=True)
                if payload:
                    body_parts.append(payload.decode("utf-8", errors="replace"))

    return {
        "file_name": eml_path.name,
        "message_id": msg.get("Message-ID"),
        "from": decode_header_value(msg.get("From")),
        "to": decode_header_value(msg.get("To")),
        "subject": decode_header_value(msg.get("Subject")),
        "received_at": received_at,
        "body": "\n".join(body_parts).strip(),
        "attachments": attachments,
    }


def read_pdf_text_and_tables(pdf_path):
    pdf_path = Path(pdf_path)
    texts = []
    tables = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
            tables.extend(page.extract_tables())

    return "\n".join(texts).strip(), tables


def find_product_table(tables):
    for table in tables:
        if not table:
            continue

        first_row = " ".join(clean_text(c) for c in table[0] if c)
        if "STT" in first_row and "Mã hàng" in first_row:
            return table

    return None


def parse_pdf_order(pdf_path):
    text, tables = read_pdf_text_and_tables(pdf_path)

    result = {
        "file_name": Path(pdf_path).name,
        "so_number": None,
        "order_date": None,
        "customer_name": None,
        "customer_tax_code": None,
        "customer_address": None,
        "total_quantity": None,
        "total_amount": None,
        "lines": [],
        "raw_text": text,
    }

    # Số đơn: BH26.0935
    m = re.search(r"BH\d{2}\.\d{4}", text)
    if m:
        result["so_number"] = m.group(0)

    # Ngày: 01/03/2026
    m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", text)
    if m:
        d, mth, y = m.group(0).split("/")
        result["order_date"] = f"{y}-{mth.zfill(2)}-{d.zfill(2)}"

    # MST
    m = re.search(r"MST:\s*(\d+)", text)
    if m:
        result["customer_tax_code"] = m.group(1)

    # Tổng tiền: lấy số tiền cuối cùng trong raw text
    money_values = re.findall(r"\d{1,3}(?:\.\d{3})+", text)
    if money_values:
        result["total_amount"] = parse_money(money_values[-1])

    # Parse từ table
    for table in tables:
        for row in table:
            cells = [clean_text(c) for c in row]

            joined = " ".join(cells)

            # Đại lý / MST nằm trong bảng thông tin
            if "MST:" in joined and len(cells) >= 4:
                result["customer_name"] = cells[1]
                result["customer_tax_code"] = re.sub(r"\D", "", cells[3]) or result["customer_tax_code"]

            # Địa chỉ
            if cells and cells[0].startswith("Địa") and len(cells) >= 2:
                result["customer_address"] = cells[1]

    product_table = find_product_table(tables)

    if product_table:
        for row in product_table[1:]:
            cells = [clean_text(c) for c in row]

            if len(cells) < 7:
                continue

            # Dòng sản phẩm thật bắt đầu bằng STT số
            if not cells[0].isdigit():
                continue

            line = {
                "stt": int(cells[0]),
                "product_code": cells[1],
                "product_name": cells[2],
                "unit": cells[3],
                "quantity": parse_money(cells[4]),
                "unit_price": parse_money(cells[5]),
                "line_total": parse_money(cells[6]),
            }

            result["lines"].append(line)

    if result["lines"]:
        result["total_quantity"] = sum(x["quantity"] or 0 for x in result["lines"])

    return result


if __name__ == "__main__":
    eml_path = "data/raw/tnbike_emails_mar2026/BH26_0935.eml"
    pdf_path = "data/raw/tnbike_pdfs_mar2026/BH26_0935.pdf"

    email_data = read_eml(eml_path)
    pdf_data = parse_pdf_order(pdf_path)

    print("===== EMAIL =====")
    print(json.dumps(email_data, ensure_ascii=False, indent=2))

    print("\n===== PDF =====")
    print(json.dumps(pdf_data, ensure_ascii=False, indent=2))