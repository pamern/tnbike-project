# ============================================================
# src/constants.py
# Global constants for TNBIKE Pipeline
# ============================================================

# ============================================================
# CSV FILE NAMES
# ============================================================

STAGING_SALES_ORDER_CSV = "staging_sales_order.csv"
STAGING_ORDER_LINE_CSV = "staging_order_line.csv"
STAGING_CUSTOMER_CSV = "staging_customer.csv"
STAGING_EMAIL_LOG_CSV = "staging_email_log.csv"
STAGING_FAIL_CSV = "staging_fail.csv"
STAGING_FAIL_SUMMARY_CSV = "staging_fail_summary.csv"

STANDARDIZED_COLOR_CSV = "standardized_color.csv"
SUCCESS_MAPPING_CUSTOMER_PROVINCE_CSV = "success_mapping_customer_province.csv"
FAILED_MAPPING_CUSTOMER_PROVINCE_CSV = "failed_mapping_customer_province.csv"

EXTRACT_FAIL_CSV = "extract_fail.csv"


# ============================================================
# SQL FILE NAMES
# ============================================================

SQL_CREATE_TABLES = "01_create_tables.sql"
SQL_IMPORT_DATA = "02_import_data.sql"
SQL_CREATE_EMAIL_LOG = "03_create_email_log.sql"
SQL_STANDARDIZE_PROVINCE = "04_standardize_province.sql"
SQL_CLEAN_PROVINCE = "05_clean_province.sql"


# ============================================================
# PROCESSING STATUS VALUES
# ============================================================

STATUS_PROCESSING = "PROCESSING"
STATUS_SUCCESS = "SUCCESS"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_FAILED = "FAILED"


# ============================================================
# MAPPING STATUS VALUES
# ============================================================

MAPPING_AUTO_MAPPED = "AUTO_MAPPED"
MAPPING_MANUAL_MAPPED = "MANUAL_MAPPED"
MAPPING_FAILED = "FAILED"
MAPPING_PENDING = "PENDING"


# ============================================================
# EXTRACTION QUALITY CHECK
# ============================================================

QUALITY_CHECK_SUCCESS = "SUCCESS"
QUALITY_CHECK_WARNING = "WARNING"
QUALITY_CHECK_FAILED = "FAILED"


# ============================================================
# DATABASE SCHEMA
# ============================================================

DEFAULT_SCHEMA = "tnbike"


# ============================================================
# LOG FILES
# ============================================================

LOG_PIPELINE = "run_pipeline.log"
LOG_PREPROCESSING = "run_preprocessing.log"
LOG_ERROR = "error.log"


# ============================================================
# RESTORE POINT
# ============================================================

RESTORE_POINT_FILE = "pipeline_restore_point.sql"
RESTORE_POINT_SUFFIX = ".sql"
