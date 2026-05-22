# ============================================================
# src/preprocessing/standardize_color.py
# Chuẩn hóa màu sản phẩm từ product_name / color
# Output:
#   data/processed/cleaned/standardized_color.csv
#
# Optional:
#   --update-db  -> cập nhật product.color và fact_sales.color
# ============================================================

import re
import sys
import argparse
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd
from psycopg2.extras import execute_batch


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.database.connection import get_cursor, get_connection, DB_SCHEMA
    from src.utils.file_utils import ensure_parent_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.database.connection import get_cursor, get_connection, DB_SCHEMA
    from src.utils.file_utils import ensure_parent_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger


logger = get_logger(__name__)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_OUTPUT_FILE = "data/processed/cleaned/standardized_color.csv"


# ============================================================
# COLOR DICTIONARY
# Key: màu chuẩn
# Value: alias có thể xuất hiện trong product_name hoặc color cũ
# ============================================================

COLOR_ALIASES: dict[str, set[str]] = {
    # Đen
    "Đen": {
        "đen", "den",
    },
    "Đen Bóng": {
        "đen bóng", "den bong",
    },
    "Đen Mờ": {
        "đen mờ", "den mo",
    },
    "Đen Nhám": {
        "đen nhám", "den nham",
    },

    # Trắng
    "Trắng": {
        "trắng", "trang",
    },
    "Trắng Da HP": {
        "trắng da hp", "trang da hp",
    },

    # Đỏ
    "Đỏ": {
        "đỏ", "do",
    },
    "Đỏ Tươi": {
        "đỏ tươi", "do tuoi",
    },
    "Đỏ Đun": {
        "đỏ đun", "do dun",
    },
    "Đỏ Đậm": {
        "đỏ đậm", "do dam",
    },

    # Xanh
    "Xanh": {
        "xanh",
    },
    "Xanh Dương": {
        "xanh dương", "xanh duong",
    },
    "Xanh Da Trời": {
        "xanh da trời", "xanh da troi",
    },
    "Xanh Nước Biển": {
        "xanh nước biển", "xanh nuoc bien",
    },
    "Xanh Lá": {
        "xanh lá", "xanh la",
    },
    "Xanh Pastel": {
        "xanh pastel", "pastel xanh",
    },
    "Xanh Mint": {
        "xanh mint", "mint",
    },
    "Xanh Ngọc": {
        "xanh ngọc", "xanh ngoc", "ngọc", "ngoc",
    },
    "Xanh Rêu": {
        "xanh rêu", "xanh reu", "rêu", "reu",
    },
    "Xanh Santorini": {
        "xanh santorini", "santorini",
    },
    "Xanh Tím": {
        "xanh tím", "xanh tim",
    },
    "Coban": {
        "coban",
    },

    # Vàng
    "Vàng": {
        "vàng", "vang",
    },
    "Vàng Chanh": {
        "vàng chanh", "vang chanh", "chanh",
    },
    "Vàng Cánh Gián": {
        "vàng cánh gián", "vang canh gian", "cánh gián", "canh gian",
    },

    # Cam
    "Cam": {
        "cam",
    },

    # Hồng
    "Hồng": {
        "hồng", "hong",
    },
    "Hồng Pastel": {
        "hồng pastel", "hong pastel", "pastel hồng", "pastel hong",
    },
    "Hồng Dạ Quang": {
        "hồng dạ quang", "hong da quang", "dạ quang hồng", "da quang hong",
    },

    # Tím
    "Tím": {
        "tím", "tim",
    },
    "Tím Dạ Quang": {
        "tím dạ quang", "tim da quang", "dạ quang tím", "da quang tim",
    },

    # Nâu
    "Nâu": {
        "nâu", "nau", "café/nâu", "cafe/nâu", "cafe nau", "ca phe nau",
    },

    # Kem
    "Kem": {
        "kem",
    },

    # Ghi / Xám
    "Ghi": {
        "ghi", "xám", "xam", "gray", "grey",
    },

    # Be
    "Be": {
        "be",
    },
}


# ============================================================
# PATTERN LOẠI BỎ KHỎI TÊN SẢN PHẨM
# Tránh nhầm mã dòng xe / brand / IP character thành màu
# ============================================================

REMOVE_PATTERNS = [
    # mã dòng, kích thước, đời xe
    r"\b\d{2,4}[-]\d{1,4}\b",
    r"\b\d{2,3}[.]?\d?\s?inch\b",
    r"\b\d{2,3}[.]?\d?\s?c\b",
    r"\b700c\b",
    r"\b27[.]?5\b",
    r"\b2[.]0\b",
    r"\b5[.]0\b",
    r"\b2023\b",
    r"\b2024\b",

    # tên dòng / cấu hình
    r"\bshimano\b",
    r"\bpro\b",
    r"\bda\s?hp\b",
    r"\btem\b",
    r"\bsuper\b",
    r"\bnew\b",
    r"\bld\b",
    r"\bmtb\b",
    r"\bgn\b",
    r"\bte\b",
    r"\bsk\b",
    r"\bgrx\b",
    r"\bspd\b",
    r"\bbase\b",
    r"\bhighway\b",
    r"\btouring\b",
    r"\bblade\b",
    r"\bcyber\b",
    r"\bcyper\b",
    r"\brex\b",
    r"\bneo\b",
    r"\broad\b",
    r"\brpd\b",
    r"\bcpd\b",
    r"\bms\b",
    r"\bm2601\b",

    # nhóm/giới tính/đặc tả
    r"\bnam\b",
    r"\bnữ\b",
    r"\bnu\b",
    r"\btruyền thống\b",
    r"\btruyen thong\b",
    r"\blốp\b",
    r"\blop\b",
    r"\bđôi\b",
    r"\bdoi\b",
    r"\bxe\b",
    r"\bđạp\b",
    r"\bdap\b",
    r"\bthống\b",
    r"\bnhất\b",
    r"\bthong\b",
    r"\bnhat\b",

    # IP / character name
    r"\bblackpink\b",
    r"\bbatman\b",
    r"\bbat\s?man\b",
    r"\bsuperman\b",
    r"\bsuper\s?man\b",
    r"\bwonder\s?woman\b",
    r"\bbat\s?wheels\b",
    r"\btom\s?&\s?jerry\b",
    r"\bwe\s?bare\s?bears\b",
    r"\bbubbles\b",
    r"\bpowerpuff\b",
    r"\bspaceboy\b",
    r"\brobot\b",
    r"\blove\b",
    r"\bpuppy\b",
    r"\bbunny\b",
    r"\bprincess\b",
    r"\bkitten\b",
    r"\bflash\b",

    # ký tự thừa
    r"[\"”“()]",
]

REMOVE_RE = re.compile("|".join(REMOVE_PATTERNS), re.IGNORECASE)


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def remove_vietnamese_accents(text: str) -> str:
    """
    Bỏ dấu tiếng Việt để match alias không dấu.
    """

    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")

    return text


def normalize_text_for_match(text: str) -> str:
    """
    Chuẩn hóa text để tìm màu:
    - lowercase
    - thay /, -, _, dấu câu bằng khoảng trắng
    - xóa pattern nhiễu
    - gom nhiều khoảng trắng
    """

    if text is None:
        return ""

    text = str(text).strip().lower()

    if text in {"", "nan", "none", "null"}:
        return ""

    text = text.replace("/", " ")
    text = re.sub(r"[-_.,;:]+", " ", text)
    text = REMOVE_RE.sub(" ", text)
    text = re.sub(r"[^a-zA-Zà-ỹÀ-ỸđĐ\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def build_match_variants(text: str) -> list[str]:
    """
    Tạo 2 bản text:
    - có dấu
    - không dấu
    """

    normalized = normalize_text_for_match(text)

    if not normalized:
        return []

    no_accent = remove_vietnamese_accents(normalized)
    no_accent = re.sub(r"\s+", " ", no_accent).strip()

    variants = [normalized]

    if no_accent != normalized:
        variants.append(no_accent)

    return variants


# ============================================================
# COLOR MATCHING
# ============================================================

def find_color_match(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Tìm màu trong text.

    Returns:
        canonical_color, matched_alias
    """

    variants = build_match_variants(text)

    if not variants:
        return None, None

    best_color = None
    best_alias = None
    best_score = -1

    for canonical_color, aliases in COLOR_ALIASES.items():
        for alias in aliases:
            alias_normalized = normalize_text_for_match(alias)
            alias_no_accent = remove_vietnamese_accents(alias_normalized)

            alias_candidates = {alias_normalized, alias_no_accent}

            for alias_candidate in alias_candidates:
                if not alias_candidate:
                    continue

                pattern = r"(^|\s)" + re.escape(alias_candidate) + r"($|\s)"

                for variant in variants:
                    if re.search(pattern, variant):
                        # Ưu tiên alias dài hơn để tránh match "xanh" trước "xanh dương"
                        score = len(alias_candidate)

                        if score > best_score:
                            best_score = score
                            best_color = canonical_color
                            best_alias = alias

    return best_color, best_alias


def standardize_product_color(
    product_name: str,
    color_old: str | None,
) -> dict:
    """
    Chuẩn hóa màu cho 1 sản phẩm.

    Ưu tiên:
        1. Tìm trong product_name
        2. Nếu không có, fallback từ color_old
    """

    color_from_name, alias_from_name = find_color_match(product_name)

    if color_from_name:
        return {
            "color_new": color_from_name,
            "mapping_status": "MATCHED_FROM_NAME",
            "match_source": "product_name",
            "matched_alias": alias_from_name,
        }

    color_from_old, alias_from_old = find_color_match(color_old or "")

    if color_from_old:
        return {
            "color_new": color_from_old,
            "mapping_status": "FALLBACK_FROM_OLD_COLOR",
            "match_source": "color_old",
            "matched_alias": alias_from_old,
        }

    return {
        "color_new": "",
        "mapping_status": "UNMAPPED",
        "match_source": "",
        "matched_alias": "",
    }


# ============================================================
# DATABASE
# ============================================================

def load_product_data() -> pd.DataFrame:
    """
    Đọc product_code, product_name, color từ DB.
    Không dùng pd.read_sql trực tiếp để tránh warning psycopg2.
    """

    query = """
        SELECT
            product_code,
            product_name,
            color
        FROM product
        ORDER BY product_code;
    """

    logger.info("Loading product data from database...")

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    df = pd.DataFrame(rows)

    if df.empty:
        logger.warning("No product data found")
        return pd.DataFrame(columns=["product_code", "product_name", "color"])

    logger.info("Loaded %s products", len(df))

    return df


def update_color_to_database(df_result: pd.DataFrame) -> None:
    """
    Cập nhật product.color và fact_sales.color.

    Quy tắc:
        - color_new rỗng -> set NULL
        - color_new có giá trị -> set màu chuẩn
    """

    logger.info("Updating product.color and fact_sales.color...")

    update_rows = []

    for _, row in df_result.iterrows():
        product_code = str(row["product_code"])
        color_new = str(row["color_new"]).strip()

        if color_new == "":
            color_value = None
        else:
            color_value = color_new

        update_rows.append((color_value, product_code))

    update_product_sql = """
        UPDATE product
        SET color = %s
        WHERE product_code = %s;
    """

    sync_fact_sales_sql = """
        UPDATE fact_sales fs
        SET color = p.color
        FROM product p
        WHERE fs.product_code = p.product_code;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_batch(cur, update_product_sql, update_rows, page_size=500)
            logger.info("Updated product.color rows: %s", cur.rowcount)

            cur.execute(sync_fact_sales_sql)
            logger.info("Synced fact_sales.color rows: %s", cur.rowcount)

    logger.info("Database color update completed")


# ============================================================
# PROCESSING
# ============================================================

def standardize_color_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Áp dụng chuẩn hóa màu cho toàn bộ DataFrame.
    """

    if df.empty:
        return pd.DataFrame(
            columns=[
                "product_code",
                "product_name",
                "color_old",
                "color_new",
                "mapping_status",
                "match_source",
                "matched_alias",
            ]
        )

    df_work = df.copy()

    df_work["product_code"] = df_work["product_code"].astype(str)
    df_work["product_name"] = df_work["product_name"].fillna("").astype(str)
    df_work["color_old"] = df_work["color"].fillna("").astype(str)

    mapped_rows = []

    for _, row in df_work.iterrows():
        mapping = standardize_product_color(
            product_name=row["product_name"],
            color_old=row["color_old"],
        )

        mapped_rows.append(mapping)

    df_mapping = pd.DataFrame(mapped_rows)

    df_result = pd.concat(
        [
            df_work[["product_code", "product_name", "color_old"]].reset_index(drop=True),
            df_mapping.reset_index(drop=True),
        ],
        axis=1,
    )

    return df_result


def export_result(df_result: pd.DataFrame, output_file: str | Path) -> Path:
    """
    Xuất kết quả chuẩn hóa ra CSV UTF-8 BOM.
    """

    output_path = ensure_parent_dir(output_file)

    df_result.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )

    logger.info("Exported cleaned color file: %s", output_path)

    return output_path


def summarize_result(df_result: pd.DataFrame) -> dict:
    """
    Tổng kết kết quả chuẩn hóa.
    """

    total_products = len(df_result)
    unmapped_count = int((df_result["color_new"].fillna("") == "").sum())
    mapped_count = total_products - unmapped_count

    status_counts = (
        df_result["mapping_status"]
        .value_counts(dropna=False)
        .to_dict()
    )

    color_counts = (
        df_result[df_result["color_new"].fillna("") != ""]
        ["color_new"]
        .value_counts()
        .head(20)
        .to_dict()
    )

    return {
        "total_products": total_products,
        "mapped_count": mapped_count,
        "unmapped_count": unmapped_count,
        "status_counts": status_counts,
        "top_colors": color_counts,
    }


def standardize_color(
    output_file: str | Path = DEFAULT_OUTPUT_FILE,
    update_db: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    Hàm chính để module khác import dùng lại.
    """

    logger.info("=" * 70)
    logger.info("STANDARDIZE COLOR STARTED")
    logger.info("=" * 70)

    df_product = load_product_data()
    df_result = standardize_color_dataframe(df_product)

    export_result(df_result, output_file)

    if update_db:
        update_color_to_database(df_result)
    else:
        logger.info("DRY RUN: database was not updated")

    summary = summarize_result(df_result)

    logger.info("=" * 70)
    logger.info("STANDARDIZE COLOR SUMMARY")
    logger.info("Total products : %s", summary["total_products"])
    logger.info("Mapped         : %s", summary["mapped_count"])
    logger.info("Unmapped       : %s", summary["unmapped_count"])
    logger.info("Status counts  : %s", summary["status_counts"])
    logger.info("Top colors     : %s", summary["top_colors"])
    logger.info("=" * 70)

    return df_result, summary


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standardize product colors for TNBIKE project"
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help="Output CSV file. Default: data/processed/cleaned/standardized_color.csv",
    )

    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Update product.color and fact_sales.color in database",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="standardize_color.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        _, summary = standardize_color(
            output_file=args.output,
            update_db=args.update_db,
        )

        print("")
        print("STANDARDIZE COLOR SUCCESS")
        print(f"Total products : {summary['total_products']}")
        print(f"Mapped         : {summary['mapped_count']}")
        print(f"Unmapped       : {summary['unmapped_count']}")
        print(f"Output         : {resolve_project_path(args.output)}")

        if args.update_db:
            print("Database       : UPDATED")
        else:
            print("Database       : NOT UPDATED - dry run")

    except Exception as e:
        logger.exception("STANDARDIZE COLOR FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()