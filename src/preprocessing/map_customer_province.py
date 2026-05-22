# ============================================================
# src/preprocessing/map_customer_province.py
# Map customer.address -> province_id
#
# Output:
#   data/processed/mapping/success_mapping_customer_province.csv
#   data/processed/mapping/failed_mapping_customer_province.csv
#
# Optional:
#   --update-db
#       Cập nhật customer.province_id
#       Đồng bộ province_id, province_name, region sang fact_sales
# ============================================================

import re
import sys
import argparse
import unicodedata
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_batch


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.database.connection import get_cursor, get_connection, DB_SCHEMA
    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.database.connection import get_cursor, get_connection, DB_SCHEMA
    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger


logger = get_logger(__name__)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_OUTPUT_DIR = "data/processed/mapping"

SUCCESS_FILE_NAME = "success_mapping_customer_province.csv"
FAILED_FILE_NAME = "failed_mapping_customer_province.csv"

# Cho phép alias ngắn nhưng an toàn.
# Không cho alias "ha", vì sẽ làm Hà Nội bị map nhầm Hà Tĩnh.
SAFE_SHORT_ALIASES = {"hcm", "hue"}


# ============================================================
# REPLACEMENT / ALIAS
# ============================================================

PROVINCE_REPLACEMENTS = {
    "TP Hồ Chí Minh": "TP. Hồ Chí Minh",
    "Thành phố Hồ Chí Minh": "TP. Hồ Chí Minh",
    "Tp Hồ Chí Minh": "TP. Hồ Chí Minh",
    "TP HCM": "TP. Hồ Chí Minh",
    "Tp HCM": "TP. Hồ Chí Minh",
    "Hồ Chí Minh": "TP. Hồ Chí Minh",
    "Sài Gòn": "TP. Hồ Chí Minh",

    "Hà Nộ": "Hà Nội",
    "Ha Noi": "Hà Nội",
    "Hà nội": "Hà Nội",

    "Nghệ A": "Nghệ An",
    "Hải Dươn": "Hải Dương",

    "TP Huế": "Thừa Thiên Huế",
    "Tp Huế": "Thừa Thiên Huế",
    "Huế": "Thừa Thiên Huế",
    "Thừa Thiên - Huế": "Thừa Thiên Huế",

    "Bà Rịa Vũng Tàu": "Bà Rịa - Vũng Tàu",
    "Bà Rịa-Vũng Tàu": "Bà Rịa - Vũng Tàu",
}


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def remove_vietnamese_accents(text: str) -> str:
    """
    Bỏ dấu tiếng Việt.
    """

    if text is None:
        return ""

    text = unicodedata.normalize("NFD", str(text))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")

    return text


def normalize_text(text: str | None) -> str:
    """
    Chuẩn hóa text để match địa chỉ:
    - lowercase
    - bỏ dấu
    - bỏ ký tự đặc biệt
    - gom khoảng trắng
    """

    if text is None or pd.isna(text):
        return ""

    text = str(text).strip().lower()

    if text in {"", "nan", "none", "null"}:
        return ""

    text = remove_vietnamese_accents(text)
    text = text.lower()

    text = re.sub(r"[-_/,.()|;:]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def apply_common_replacements(address: str | None) -> str:
    """
    Sửa một số lỗi tên tỉnh/thành phổ biến trước khi normalize.
    """

    if address is None or pd.isna(address):
        return ""

    address_text = str(address)

    for wrong, correct in PROVINCE_REPLACEMENTS.items():
        address_text = address_text.replace(wrong, correct)

    return address_text


def normalize_alias(alias: str) -> str:
    """
    Chuẩn hóa alias tỉnh/thành.

    Chỉ xóa tiền tố nếu nằm ở đầu chuỗi:
        tinh ha tinh       -> ha tinh
        thanh pho ha noi   -> ha noi
        tp ho chi minh     -> ho chi minh

    Không được xóa chữ 'tinh' ở giữa/cuối vì:
        ha tinh -> ha là sai.
    """

    alias = normalize_text(alias)

    alias = re.sub(r"^tinh\s+", "", alias)
    alias = re.sub(r"^thanh pho\s+", "", alias)
    alias = re.sub(r"^tp\s+", "", alias)

    alias = re.sub(r"\s+", " ", alias).strip()

    return alias


# ============================================================
# LOAD DATA
# ============================================================

def load_province_data() -> pd.DataFrame:
    """
    Đọc bảng province.
    """

    query = """
        SELECT
            province_id,
            province_name,
            region
        FROM province
        ORDER BY province_name;
    """

    logger.info("Loading province data...")

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    df = pd.DataFrame(rows)

    if df.empty:
        raise ValueError("province table is empty")

    logger.info("Loaded provinces: %s", len(df))

    return df


def load_customer_data(only_missing: bool = False) -> pd.DataFrame:
    """
    Đọc customer có address.

    Args:
        only_missing:
            True  -> chỉ map customer đang thiếu province_id
            False -> map toàn bộ customer có address
    """

    if only_missing:
        where_clause = """
            WHERE address IS NOT NULL
              AND TRIM(address) <> ''
              AND province_id IS NULL
        """
    else:
        where_clause = """
            WHERE address IS NOT NULL
              AND TRIM(address) <> ''
        """

    query = f"""
        SELECT
            customer_code,
            customer_name,
            tax_code,
            address,
            province_id AS province_id_old
        FROM customer
        {where_clause}
        ORDER BY customer_code;
    """

    logger.info("Loading customer data... only_missing=%s", only_missing)

    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    df = pd.DataFrame(rows)

    if df.empty:
        logger.warning("No customer data found for province mapping")
        return pd.DataFrame(
            columns=[
                "customer_code",
                "customer_name",
                "tax_code",
                "address",
                "province_id_old",
            ]
        )

    logger.info("Loaded customers: %s", len(df))

    return df


# ============================================================
# PROVINCE LOOKUP
# ============================================================

def is_safe_alias(alias: str) -> bool:
    """
    Chặn alias quá ngắn để tránh match sai.

    Ví dụ:
        Hà Tĩnh -> ha tinh, tuyệt đối không tạo alias 'ha'.
    """

    if not alias:
        return False

    if len(alias) >= 4:
        return True

    return alias in SAFE_SHORT_ALIASES


def build_province_lookup(province_df: pd.DataFrame) -> list[dict]:
    """
    Tạo danh sách lookup province.

    Có 2 nhóm alias:
        primary: alias từ province_name
        extra  : alias bổ sung như hcm, sai gon, hue...

    Sort alias dài trước để tránh match sai.
    """

    lookup_rows = []

    for _, row in province_df.iterrows():
        province_id = int(row["province_id"])
        province_name = str(row["province_name"])
        region = row.get("region", None)

        aliases = []

        # Alias chính
        primary_alias = normalize_text(province_name)
        no_prefix_alias = normalize_alias(province_name)

        if primary_alias:
            aliases.append((primary_alias, "primary"))

        if no_prefix_alias and no_prefix_alias != primary_alias:
            aliases.append((no_prefix_alias, "primary"))

        # Alias bổ sung riêng
        extra_aliases = set()

        if province_name == "TP. Hồ Chí Minh":
            extra_aliases.update(
                {
                    "tp ho chi minh",
                    "thanh pho ho chi minh",
                    "ho chi minh",
                    "hcm",
                    "tp hcm",
                    "sai gon",
                }
            )

        elif province_name == "Hà Nội":
            extra_aliases.update(
                {
                    "ha noi",
                    "tp ha noi",
                    "thanh pho ha noi",
                }
            )

        elif province_name == "Thừa Thiên Huế":
            extra_aliases.update(
                {
                    "thua thien hue",
                    "hue",
                    "tp hue",
                    "thanh pho hue",
                }
            )

        elif province_name == "Bà Rịa - Vũng Tàu":
            extra_aliases.update(
                {
                    "ba ria vung tau",
                    "vung tau",
                }
            )

        for alias in extra_aliases:
            alias_clean = normalize_text(alias)

            if alias_clean:
                aliases.append((alias_clean, "extra"))

        # Remove duplicate + chặn alias ngắn nguy hiểm
        seen = set()

        for alias, alias_type in aliases:
            alias = re.sub(r"\s+", " ", alias).strip()

            if not alias or alias in seen:
                continue

            if not is_safe_alias(alias):
                logger.debug(
                    "Skip unsafe short alias: province=%s, alias=%s",
                    province_name,
                    alias,
                )
                continue

            seen.add(alias)

            lookup_rows.append(
                {
                    "province_id": province_id,
                    "province_name": province_name,
                    "region": region,
                    "alias": alias,
                    "alias_type": alias_type,
                    "alias_len": len(alias),
                }
            )

    lookup_rows = sorted(
        lookup_rows,
        key=lambda x: x["alias_len"],
        reverse=True,
    )

    logger.info("Built province aliases: %s", len(lookup_rows))

    return lookup_rows


# ============================================================
# MAPPING LOGIC
# ============================================================

def extract_province_from_address(
    address: str | None,
    province_lookup: list[dict],
) -> dict:
    """
    Extract province từ address.

    Logic:
        1. Regex boundary trên toàn bộ alias
        2. Fallback substring trên primary alias giống logic cũ
        3. Fallback substring trên extra alias an toàn
    """

    if address is None or pd.isna(address) or str(address).strip() == "":
        return {
            "province_id": None,
            "province_name_extract": None,
            "region": None,
            "matched_alias": None,
            "mapping_status": "EMPTY_ADDRESS",
        }

    address_fixed = apply_common_replacements(address)
    address_clean = normalize_text(address_fixed)

    if not address_clean:
        return {
            "province_id": None,
            "province_name_extract": None,
            "region": None,
            "matched_alias": None,
            "mapping_status": "EMPTY_ADDRESS",
        }

    padded_address = f" {address_clean} "

    # ========================================================
    # PASS 1: strict regex boundary
    # ========================================================
    for item in province_lookup:
        alias = item["alias"]
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"

        if re.search(pattern, padded_address):
            return {
                "province_id": item["province_id"],
                "province_name_extract": item["province_name"],
                "region": item["region"],
                "matched_alias": alias,
                "mapping_status": "MATCHED_REGEX",
            }

    # ========================================================
    # PASS 2: substring giống logic cũ, nhưng chỉ với alias an toàn
    # ========================================================
    for item in province_lookup:
        alias = item["alias"]

        if item["alias_type"] == "primary" and alias in address_clean:
            return {
                "province_id": item["province_id"],
                "province_name_extract": item["province_name"],
                "region": item["region"],
                "matched_alias": alias,
                "mapping_status": "MATCHED_SUBSTRING_PRIMARY",
            }

    # ========================================================
    # PASS 3: extra substring an toàn
    # ========================================================
    for item in province_lookup:
        alias = item["alias"]

        if item["alias_type"] == "extra" and alias in address_clean:
            return {
                "province_id": item["province_id"],
                "province_name_extract": item["province_name"],
                "region": item["region"],
                "matched_alias": alias,
                "mapping_status": "MATCHED_SUBSTRING_EXTRA",
            }

    return {
        "province_id": None,
        "province_name_extract": None,
        "region": None,
        "matched_alias": None,
        "mapping_status": "UNMAPPED",
    }


def map_customer_province_dataframe(
    customer_df: pd.DataFrame,
    province_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Map customer.address sang province.
    """

    if customer_df.empty:
        return pd.DataFrame(
            columns=[
                "customer_code",
                "customer_name",
                "tax_code",
                "address",
                "province_id_old",
                "province_id",
                "province_name_extract",
                "region",
                "matched_alias",
                "mapping_status",
            ]
        )

    province_lookup = build_province_lookup(province_df)

    mapped_rows = []

    logger.info("Mapping customer address to province...")

    for _, row in customer_df.iterrows():
        mapping = extract_province_from_address(
            address=row["address"],
            province_lookup=province_lookup,
        )

        mapped_rows.append(mapping)

    mapping_df = pd.DataFrame(mapped_rows)

    result_df = pd.concat(
        [
            customer_df.reset_index(drop=True),
            mapping_df.reset_index(drop=True),
        ],
        axis=1,
    )

    return result_df


# ============================================================
# EXPORT
# ============================================================

def split_success_failed(result_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tách success / failed.
    """

    success_df = result_df[result_df["province_id"].notna()].copy()
    failed_df = result_df[result_df["province_id"].isna()].copy()

    return success_df, failed_df


def export_mapping_files(
    success_df: pd.DataFrame,
    failed_df: pd.DataFrame,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Path]:
    """
    Xuất success/failed CSV.
    """

    output_dir = ensure_dir(output_dir)

    success_path = output_dir / SUCCESS_FILE_NAME
    failed_path = output_dir / FAILED_FILE_NAME

    success_df.to_csv(success_path, index=False, encoding="utf-8-sig")
    failed_df.to_csv(failed_path, index=False, encoding="utf-8-sig")

    logger.info("Exported success mapping file: %s", success_path)
    logger.info("Exported failed mapping file : %s", failed_path)

    return {
        "success_file": success_path,
        "failed_file": failed_path,
    }


# ============================================================
# DATABASE UPDATE
# ============================================================

def update_customer_province_to_db(
    success_df: pd.DataFrame,
    reset_before_update: bool = False,
) -> None:
    """
    Cập nhật customer.province_id theo mapping thành công.
    Sau đó đồng bộ province sang fact_sales.
    """

    if success_df.empty:
        logger.warning("No success mapping rows to update database")
        return

    update_rows = []

    for _, row in success_df.iterrows():
        update_rows.append(
            (
                int(row["province_id"]),
                str(row["customer_code"]),
            )
        )

    update_customer_sql = """
        UPDATE customer
        SET province_id = %s,
            updated_at = NOW()
        WHERE customer_code = %s;
    """

    reset_customer_sql = """
        UPDATE customer
        SET province_id = NULL,
            updated_at = NOW();
    """

    sync_fact_sales_sql = """
        UPDATE fact_sales fs
        SET
            province_id = c.province_id,
            province_name = p.province_name,
            region = p.region
        FROM customer c
        LEFT JOIN province p
            ON p.province_id = c.province_id
        WHERE fs.customer_code = c.customer_code;
    """

    logger.info("Updating customer.province_id...")

    with get_connection() as conn:
        with conn.cursor() as cur:
            if reset_before_update:
                logger.warning("Resetting all customer.province_id before update...")
                cur.execute(reset_customer_sql)
                logger.info("Reset customer.province_id rows: %s", cur.rowcount)

            execute_batch(cur, update_customer_sql, update_rows, page_size=500)
            logger.info("Updated customer rows: %s", len(update_rows))

            logger.info("Syncing province fields to fact_sales...")
            cur.execute(sync_fact_sales_sql)
            logger.info("Synced fact_sales rows: %s", cur.rowcount)

    logger.info("Database update completed")


# ============================================================
# SUMMARY
# ============================================================

def summarize_mapping(result_df: pd.DataFrame) -> dict:
    """
    Tổng kết mapping.
    """

    total = len(result_df)

    if total == 0:
        return {
            "total_customers": 0,
            "success_count": 0,
            "failed_count": 0,
            "success_rate": 0,
            "status_counts": {},
            "region_counts": {},
        }

    success_count = int(result_df["province_id"].notna().sum())
    failed_count = total - success_count
    success_rate = success_count / total * 100

    status_counts = result_df["mapping_status"].value_counts(dropna=False).to_dict()

    region_counts = (
        result_df[result_df["province_id"].notna()]
        ["region"]
        .value_counts(dropna=False)
        .to_dict()
    )

    return {
        "total_customers": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": success_rate,
        "status_counts": status_counts,
        "region_counts": region_counts,
    }


def log_failed_examples(failed_df: pd.DataFrame, limit: int = 10) -> None:
    """
    Log một số dòng failed để kiểm tra.
    """

    if failed_df.empty:
        return

    logger.warning("Some customers were not mapped. Preview:")

    preview = failed_df[
        [
            "customer_code",
            "customer_name",
            "address",
            "mapping_status",
        ]
    ].head(limit)

    for _, row in preview.iterrows():
        logger.warning(
            "%s | %s | %s | %s",
            row["customer_code"],
            row["customer_name"],
            row["address"],
            row["mapping_status"],
        )


# ============================================================
# MAIN PROCESS
# ============================================================

def map_customer_province(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    update_db: bool = False,
    only_missing: bool = False,
    reset_before_update: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    Hàm chính để module khác import dùng lại.
    """

    logger.info("=" * 70)
    logger.info("MAP CUSTOMER PROVINCE STARTED")
    logger.info("=" * 70)
    logger.info("Output dir          : %s", resolve_project_path(output_dir))
    logger.info("Update DB           : %s", update_db)
    logger.info("Only missing        : %s", only_missing)
    logger.info("Reset before update : %s", reset_before_update)
    logger.info("Schema              : %s", DB_SCHEMA)
    logger.info("=" * 70)

    province_df = load_province_data()
    customer_df = load_customer_data(only_missing=only_missing)

    result_df = map_customer_province_dataframe(
        customer_df=customer_df,
        province_df=province_df,
    )

    success_df, failed_df = split_success_failed(result_df)

    export_mapping_files(
        success_df=success_df,
        failed_df=failed_df,
        output_dir=output_dir,
    )

    summary = summarize_mapping(result_df)

    logger.info("=" * 70)
    logger.info("MAP CUSTOMER PROVINCE SUMMARY")
    logger.info("Total customers : %s", summary["total_customers"])
    logger.info("Success         : %s", summary["success_count"])
    logger.info("Failed          : %s", summary["failed_count"])
    logger.info("Success rate    : %.2f%%", summary["success_rate"])
    logger.info("Status counts   : %s", summary["status_counts"])
    logger.info("Region counts   : %s", summary["region_counts"])
    logger.info("=" * 70)

    log_failed_examples(failed_df)

    if update_db:
        update_customer_province_to_db(
            success_df=success_df,
            reset_before_update=reset_before_update,
        )
    else:
        logger.info("DRY RUN: database was not updated")

    return result_df, summary


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map customer address to province for TNBIKE project"
    )

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for mapping CSV files",
    )

    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Update customer.province_id and sync province fields to fact_sales",
    )

    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only map customers with province_id IS NULL",
    )

    parser.add_argument(
        "--reset-before-update",
        action="store_true",
        help="Reset all customer.province_id before applying successful mappings",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="map_customer_province.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        _, summary = map_customer_province(
            output_dir=args.output_dir,
            update_db=args.update_db,
            only_missing=args.only_missing,
            reset_before_update=args.reset_before_update,
        )

        print("")
        print("MAP CUSTOMER PROVINCE SUCCESS")
        print(f"Total customers : {summary['total_customers']}")
        print(f"Success         : {summary['success_count']}")
        print(f"Failed          : {summary['failed_count']}")
        print(f"Success rate    : {summary['success_rate']:.2f}%")
        print(f"Output dir      : {resolve_project_path(args.output_dir)}")

        if args.update_db:
            print("Database        : UPDATED")
        else:
            print("Database        : NOT UPDATED - dry run")

    except Exception as e:
        logger.exception("MAP CUSTOMER PROVINCE FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()