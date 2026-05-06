from pathlib import Path
from email import policy
from email.parser import BytesParser
from email.header import decode_header
from email.utils import parsedate_to_datetime


def decode_header_value(value):
    if not value:
        return ""

    result = ""
    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8", errors="replace")
        else:
            result += part

    return result.strip()


def read_eml_content(eml_path):
    eml_path = Path(eml_path)

    with eml_path.open("rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    raw_date = msg.get("Date")
    try:
        received_at = parsedate_to_datetime(raw_date).isoformat() if raw_date else ""
    except Exception:
        received_at = raw_date or ""

    body_parts = []
    attachments = []

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()

        if disposition == "attachment":
            filename = decode_header_value(part.get_filename())
            attachments.append(filename)
            continue

        if content_type == "text/plain":
            try:
                body_parts.append(part.get_content())
            except Exception:
                payload = part.get_payload(decode=True)
                if payload:
                    body_parts.append(payload.decode("utf-8", errors="replace"))

    return {
        "file_name": eml_path.name,
        "message_id": msg.get("Message-ID") or "",
        "from": decode_header_value(msg.get("From")),
        "to": decode_header_value(msg.get("To")),
        "subject": decode_header_value(msg.get("Subject")),
        "date": received_at,
        "body": "\n".join(body_parts).strip(),
        "attachments": attachments,
    }


def export_eml_to_txt(eml_path, txt_path):
    data = read_eml_content(eml_path)

    content = f"""File: {data["file_name"]}
Message-ID: {data["message_id"]}
From: {data["from"]}
To: {data["to"]}
Subject: {data["subject"]}
Date: {data["date"]}

Attachments:
{chr(10).join("- " + a for a in data["attachments"]) if data["attachments"] else "(none)"}

Body:
{data["body"]}
"""

    txt_path = Path(txt_path)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(content, encoding="utf-8")

    return txt_path


if __name__ == "__main__":
    eml_path = "data/raw/tnbike_emails_mar2026/BH26_0935.eml"
    txt_path = "data/processed/txt/BH26_0935.txt"

    output = export_eml_to_txt(eml_path, txt_path)
    print(f"Exported to: {output}")