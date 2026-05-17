from pathlib import Path
import os
import re
import unicodedata
import logging

import pandas as pd
import psycopg2
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

DRY_TRY = True

OUTPUT_DIR = Path(r"data\processed\cleaned")
OUTPUT_FILE = OUTPUT_DIR / "map_product_line.csv"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("map_product_line_exact")


# ============================================================
# DB CONFIG
# ============================================================

DB_CONFIG = {
    "host": os.getenv("PGHOST", os.getenv("DB_HOST", "localhost")),
    "port": os.getenv("PGPORT", os.getenv("DB_PORT", "5432")),
    "database": os.getenv("PGDATABASE", os.getenv("DB_NAME", "tnbike_db")),
    "user": os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
}


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def clean_text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def remove_vietnamese_accents(value: str) -> str:
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "D")
    return value


def normalize_text(value) -> str:
    value = clean_text(value).lower()
    value = remove_vietnamese_accents(value)

    value = value.replace("_", " ")
    value = value.replace("-", " ")
    value = value.replace("/", " ")
    value = value.replace(",", ".")
    value = value.replace('"', " ")
    value = value.replace("'", " ")

    # Giữ chữ, số, dấu chấm vì có 2.0, 27.5, 700C
    value = re.sub(r"[^a-z0-9.\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value


def compact_text(value) -> str:
    value = normalize_text(value)
    return re.sub(r"\s+", "", value)


def remove_common_product_words(value: str) -> str:
    value = normalize_text(value)

    stop_phrases = [
        "xe dap thong nhat",
        "thong nhat",
        "xe dap",
    ]

    for phrase in stop_phrases:
        value = value.replace(phrase, " ")

    value = re.sub(r"\s+", " ", value).strip()
    return value


# ============================================================
# LINE ALIASES - EXACT ONLY
# ============================================================

def build_line_aliases(line_name: str) -> list[str]:
    """
    Tạo alias để exact match 100%.
    Không dùng fuzzy.
    Nếu alias xuất hiện nguyên cụm trong product_name đã chuẩn hóa thì AUTO_MAPPED.
    """

    base = normalize_text(line_name)

    aliases = set()

    if base:
        aliases.add(base)

    # Bỏ chữ "xe" đầu dòng
    no_xe = re.sub(r"^xe\s+", "", base).strip()
    if no_xe:
        aliases.add(no_xe)

    # Bỏ phần trong ngoặc, ví dụ: (IP - Bản quyền)
    no_parentheses = re.sub(r"\(.*?\)", " ", base)
    no_parentheses = re.sub(r"\s+", " ", no_parentheses).strip()

    if no_parentheses:
        aliases.add(no_parentheses)

    no_parentheses_no_xe = re.sub(r"^xe\s+", "", no_parentheses).strip()
    if no_parentheses_no_xe:
        aliases.add(no_parentheses_no_xe)

    # Chuẩn hóa cách ghi 27.5 / 27 5
    extra_aliases = set()

    for alias in aliases:
        extra_aliases.add(alias.replace("27 5", "27.5"))
        extra_aliases.add(alias.replace("27.5", "27 5"))

        # Cyber/Cyper là lỗi tên sản phẩm khá thường gặp trong dữ liệu
        extra_aliases.add(alias.replace("cyber", "cyper"))
        extra_aliases.add(alias.replace("cyper", "cyber"))

    aliases.update(extra_aliases)

    aliases = {a for a in aliases if a}

    return sorted(aliases, key=len, reverse=True)


def exact_match_line(product_name: str, line_name: str) -> tuple[bool, str, str]:
    """
    Chỉ match 100%.

    Return:
    - is_match
    - match_method
    - matched_alias
    """

    product_norm = remove_common_product_words(product_name)
    product_compact = compact_text(product_norm)

    aliases = build_line_aliases(line_name)

    for alias in aliases:
        alias_norm = normalize_text(alias)
        alias_compact = compact_text(alias_norm)

        if not alias_norm:
            continue

        # Match nguyên cụm sau chuẩn hóa
        if alias_norm in product_norm:
            return True, "exact_contains_alias", alias_norm

        # Match compact để xử lý khác biệt khoảng trắng/ký tự ngăn cách
        if alias_compact and alias_compact in product_compact:
            return True, "exact_contains_compact_alias", alias_norm

    return False, "no_exact_match", ""


# ============================================================
# DB READ
# ============================================================

def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def read_products_and_lines():
    product_sql = """
        SELECT
            p.product_code,
            p.product_name
        FROM tnbike.product p
        WHERE p.line_id IS NULL
        ORDER BY p.product_code;
    """

    line_sql = """
        SELECT
            pl.line_id,
            pl.line_name,
            pl.group_code,
            pg.group_name
        FROM tnbike.product_line pl
        LEFT JOIN tnbike.product_group pg
            ON pl.group_code = pg.group_code
        ORDER BY pl.line_id;
    """

    logger.info("=" * 80)
    logger.info("READ DATA FROM DATABASE")
    logger.info("Mode: chỉ xử lý product có line_id IS NULL")
    logger.info(
        "Connecting to database: %s:%s/%s",
        DB_CONFIG["host"],
        DB_CONFIG["port"],
        DB_CONFIG["database"],
    )

    with get_connection() as conn:
        df_product = pd.read_sql_query(product_sql, conn, dtype={"product_code": str})
        df_line = pd.read_sql_query(line_sql, conn)

    df_product["product_code"] = df_product["product_code"].astype(str)

    logger.info("Loaded products with NULL line_id: %s", len(df_product))
    logger.info("Loaded product lines: %s", len(df_line))
    logger.info("=" * 80)

    return df_product, df_line


# ============================================================
# MAPPING - EXACT 100 ONLY
# ============================================================

def map_product_lines(df_product: pd.DataFrame, df_line: pd.DataFrame) -> pd.DataFrame:
    results = []

    total = len(df_product)
    line_records = df_line.to_dict("records")

    logger.info("=" * 80)
    logger.info("START EXACT MAPPING PRODUCT -> PRODUCT_LINE")
    logger.info("Rule: AUTO_MAPPED only when exact normalized alias is found")
    logger.info("If multiple lines match 100%, choose the longest line_name")
    logger.info("No fuzzy score, no review threshold")
    logger.info("=" * 80)

    for idx, product in enumerate(df_product.to_dict("records"), start=1):
        product_code = clean_text(product.get("product_code"))
        product_name = clean_text(product.get("product_name"))

        matched_lines = []

        for line in line_records:
            is_match, match_method, matched_alias = exact_match_line(
                product_name=product_name,
                line_name=line["line_name"],
            )

            if is_match:
                line_name_clean = clean_text(line["line_name"])
                line_name_norm = normalize_text(line_name_clean)
                line_name_compact = compact_text(line_name_clean)

                matched_lines.append(
                    {
                        "line_id": int(line["line_id"]),
                        "line_name": line_name_clean,
                        "group_code": line["group_code"],
                        "group_name": line["group_name"],
                        "match_method": match_method,
                        "matched_alias": matched_alias,
                        "line_name_length": len(line_name_norm),
                        "line_name_compact_length": len(line_name_compact),
                        "alias_length": len(matched_alias),
                    }
                )

        if matched_lines:
            # Ưu tiên:
            # 1. line_name compact dài nhất
            # 2. line_name normalized dài nhất
            # 3. matched_alias dài nhất
            # 4. line_id nhỏ hơn để kết quả ổn định nếu vẫn hòa
            matched_lines = sorted(
                matched_lines,
                key=lambda x: (
                    x["line_name_compact_length"],
                    x["line_name_length"],
                    x["alias_length"],
                    -int(x["line_id"]),
                ),
                reverse=True,
            )

            best = matched_lines[0]

            mapped_line_id = best["line_id"]
            mapped_line_name = best["line_name"]

            mapping_status = "AUTO_MAPPED"
            matched_count = len(matched_lines)

        else:
            mapped_line_id = ""
            mapped_line_name = ""

            mapping_status = "UNMAPPED"
            matched_count = 0

        row = {
            "product_code": product_code,
            "product_name": product_name,
            "mapped_line_id": mapped_line_id,
            "mapped_line_name": mapped_line_name,
        }

        results.append(row)

        logger.info(
            "MAPPED %s/%s | %s | status=%s | mapped_line=%s | matched_count=%s",
            idx,
            total,
            product_code,
            mapping_status,
            mapped_line_name if mapped_line_name else "NONE",
            matched_count,
        )

    df_result = pd.DataFrame(
        results,
        columns=[
            "product_code",
            "product_name",
            "mapped_line_id",
            "mapped_line_name",
        ],
    )

    mapped_count = df_result["mapped_line_id"].astype(str).str.strip().ne("").sum()
    unmapped_count = len(df_result) - mapped_count

    logger.info("=" * 80)
    logger.info("EXACT MAPPING FINISHED")
    logger.info("Total rows: %s", len(df_result))
    logger.info("AUTO_MAPPED: %s", mapped_count)
    logger.info("UNMAPPED: %s", unmapped_count)
    logger.info("=" * 80)

    return df_result


# ============================================================
# EXPORT
# ============================================================

def export_result(df_result: pd.DataFrame):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_columns = [
        "product_code",
        "product_name",
        "mapped_line_id",
        "mapped_line_name",
    ]

    if df_result.empty:
        logger.warning("Không có dữ liệu để export. Vẫn tạo file CSV rỗng.")
        df_result = pd.DataFrame(columns=output_columns)
    else:
        for col in output_columns:
            if col not in df_result.columns:
                df_result[col] = ""

        df_result = df_result[output_columns]

    df_result.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    mapped_count = df_result["mapped_line_id"].astype(str).str.strip().ne("").sum()
    unmapped_count = len(df_result) - mapped_count

    logger.info("Exported mapping file: %s", OUTPUT_FILE)
    logger.info("Rows exported: %s", len(df_result))

    print("\n==============================")
    print("MAP PRODUCT LINE SUMMARY")
    print("==============================")
    print(f"Tổng SKU line_id NULL: {len(df_result)}")
    print(f"Số SKU map được: {mapped_count}")
    print(f"Số SKU chưa map được: {unmapped_count}")
    print("\nOutput file:")
    print(OUTPUT_FILE)

    return df_result


# ============================================================
# UPDATE DB
# ============================================================

def update_database(df_result: pd.DataFrame):
    df_mapped = df_result.copy()

    df_mapped["mapped_line_id"] = df_mapped["mapped_line_id"].astype(str).str.strip()
    df_mapped = df_mapped[df_mapped["mapped_line_id"] != ""]

    if df_mapped.empty:
        logger.warning("Không có SKU nào map được. Không update DB.")
        print("[UPDATE] Không có SKU nào map được. Không update DB.")
        return

    records = [
        (
            str(row["product_code"]),
            int(row["mapped_line_id"]),
        )
        for _, row in df_mapped.iterrows()
    ]

    conn = None

    try:
        conn = get_connection()

        with conn.cursor() as cur:
            cur.execute("SET search_path TO tnbike, public;")

            cur.execute("""
                CREATE TEMP TABLE tmp_map_product_line (
                    product_code VARCHAR(20) PRIMARY KEY,
                    mapped_line_id INTEGER NOT NULL
                ) ON COMMIT DROP;
            """)

            cur.executemany("""
                INSERT INTO tmp_map_product_line (
                    product_code,
                    mapped_line_id
                )
                VALUES (%s, %s);
            """, records)

            # 1. Update product.line_id
            cur.execute("""
                UPDATE product p
                SET line_id = tmp.mapped_line_id
                FROM tmp_map_product_line tmp
                WHERE p.product_code = tmp.product_code
                  AND p.line_id IS NULL;
            """)

            updated_product_count = cur.rowcount

            # 2. Update fact_sales theo product_line + product_group
            cur.execute("""
                UPDATE fact_sales fs
                SET
                    line_id_fk = pl.line_id,
                    line_name = pl.line_name,
                    group_code = pl.group_code,
                    group_name = pg.group_name
                FROM product p
                JOIN product_line pl
                    ON pl.line_id = p.line_id
                JOIN product_group pg
                    ON pg.group_code = pl.group_code
                WHERE fs.product_code = p.product_code
                  AND p.product_code IN (
                        SELECT product_code
                        FROM tmp_map_product_line
                  );
            """)

            updated_fact_count = cur.rowcount

        conn.commit()

        print("\n==============================")
        print("UPDATE PRODUCT LINE SUMMARY")
        print("==============================")
        print(f"Đã update product: {updated_product_count} SKU")
        print(f"Đã update fact_sales: {updated_fact_count} dòng")

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


# ============================================================
# MAIN
# ============================================================

def main():
    logger.info("=" * 80)
    logger.info("START PRODUCT LINE EXACT MAPPING SCRIPT")
    logger.info("Only products where product.line_id IS NULL will be processed")
    logger.info("AUTO_MAPPED only when exact match = 100%")
    logger.info("If multiple exact matches exist, choose the longest line_name")
    logger.info("DRY_TRY = %s", DRY_TRY)
    logger.info("=" * 80)

    df_product, df_line = read_products_and_lines()

    if df_line.empty:
        logger.error("Bảng product_line rỗng. Không thể map.")
        return

    if df_product.empty:
        logger.warning("Không có product nào có line_id IS NULL để map.")
        df_result = pd.DataFrame()
        export_result(df_result)
        return

    df_result = map_product_lines(df_product, df_line)

    if DRY_TRY:
        export_result(df_result)
    else:
        update_database(df_result)

    logger.info("DONE")


if __name__ == "__main__":
    main()