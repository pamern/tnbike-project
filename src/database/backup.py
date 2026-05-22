# ============================================================
# src/database/backup.py
# Backup / Restore PostgreSQL database TNBIKE
# Hỗ trợ:
#   - .sql      : pg_dump plain SQL      -> restore bằng psql
#   - .dump     : pg_dump custom format  -> restore bằng pg_restore
#   - .sql.gz   : SQL nén gzip
#   - .dump.gz  : custom dump nén gzip
# ============================================================

import os
import sys
import gzip
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.database.connection import (
        DB_NAME,
        DB_USER,
        DB_PASSWORD,
        DB_HOST,
        DB_PORT,
        DB_SCHEMA,
    )
    from src.utils.file_utils import ensure_dir, ensure_parent_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.database.connection import (
        DB_NAME,
        DB_USER,
        DB_PASSWORD,
        DB_HOST,
        DB_PORT,
        DB_SCHEMA,
    )
    from src.utils.file_utils import ensure_dir, ensure_parent_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger


# ============================================================
# CONFIG RIÊNG CHO BACKUP / RESTORE
# ============================================================

POSTGRES_CONTAINER = os.getenv("POSTGRES_CONTAINER", "tnbike_postgres")

DEFAULT_BACKUP_DIR = "data/backup/restore_db"
DEFAULT_BACKUP_STEM = "tnbike_db_backup"

VALID_RESTORE_SUFFIXES = (
    ".sql",
    ".sql.gz",
    ".dump",
    ".dump.gz",
)

logger = get_logger(__name__)


# ============================================================
# SUBPROCESS HELPER
# ============================================================

def run_command(
    command: list[str],
    description: str,
    env: dict | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """
    Chạy command bằng subprocess và log kết quả.
    """

    logger.info("Running: %s", description)
    logger.debug("Command: %s", " ".join(command))

    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    if result.stdout:
        logger.info(result.stdout.strip())

    if result.stderr:
        if result.returncode == 0:
            logger.info(result.stderr.strip())
        else:
            logger.error(result.stderr.strip())

    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {description}")

    return result


# ============================================================
# VALIDATION
# ============================================================

def check_docker_available() -> None:
    """
    Kiểm tra Docker CLI có dùng được không.
    """

    run_command(
        ["docker", "--version"],
        "Check Docker CLI",
    )


def check_container_running(container_name: str) -> None:
    """
    Kiểm tra container PostgreSQL có đang chạy không.
    """

    result = run_command(
        [
            "docker",
            "ps",
            "--filter",
            f"name={container_name}",
            "--format",
            "{{.Names}}",
        ],
        f"Check container running: {container_name}",
    )

    running_containers = result.stdout.strip().splitlines()

    if container_name not in running_containers:
        raise RuntimeError(
            f"Container '{container_name}' is not running. "
            f"Run: docker compose up -d"
        )


def validate_restore_file(input_path: str | Path) -> Path:
    """
    Kiểm tra file restore có tồn tại không.
    Hỗ trợ:
        .sql
        .sql.gz
        .dump
        .dump.gz
    """

    input_path = resolve_project_path(input_path)

    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"Restore file not found: {input_path}")

    if not input_path.name.lower().endswith(VALID_RESTORE_SUFFIXES):
        raise ValueError("Restore file must be .sql, .sql.gz, .dump, or .dump.gz")

    return input_path


def detect_backup_format(file_path: str | Path) -> str:
    """
    Nhận diện format backup theo tên file.

    Returns:
        "sql"  -> .sql hoặc .sql.gz
        "dump" -> .dump hoặc .dump.gz
    """

    name = Path(file_path).name.lower()

    if name.endswith(".sql") or name.endswith(".sql.gz"):
        return "sql"

    if name.endswith(".dump") or name.endswith(".dump.gz"):
        return "dump"

    raise ValueError("Unsupported backup format. Use .sql, .sql.gz, .dump, or .dump.gz")


# ============================================================
# PATH HELPERS
# ============================================================

def build_backup_path(
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    with_timestamp: bool = False,
    backup_format: str = "sql",
) -> Path:
    """
    Tạo đường dẫn file backup.

    backup_format:
        sql  -> .sql
        dump -> .dump
    """

    if backup_format not in ["sql", "dump"]:
        raise ValueError("backup_format must be 'sql' or 'dump'")

    backup_dir = ensure_dir(backup_dir)

    ext = "sql" if backup_format == "sql" else "dump"

    if with_timestamp:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{DEFAULT_BACKUP_STEM}_{timestamp}.{ext}"
    else:
        filename = f"{DEFAULT_BACKUP_STEM}.{ext}"

    output_path = backup_dir / filename

    return ensure_parent_dir(output_path)


def normalize_backup_output_path(
    output_path: str | Path | None,
    with_timestamp: bool,
    backup_format: str,
    gzip_output: bool,
) -> tuple[Path, str, bool]:
    """
    Chuẩn hóa output path trước khi pg_dump.

    Nếu user truyền output là .sql.gz hoặc .dump.gz:
        - pg_dump sẽ ghi ra file tạm .sql hoặc .dump
        - sau đó gzip thành .gz
    """

    if output_path is None:
        final_path = build_backup_path(
            with_timestamp=with_timestamp,
            backup_format=backup_format,
        )

        return final_path, backup_format, gzip_output

    final_path = ensure_parent_dir(output_path)
    detected_format = detect_backup_format(final_path)

    if final_path.name.lower().endswith(".gz"):
        gzip_output = True
        final_path = final_path.with_suffix("")

    return final_path, detected_format, gzip_output


def get_latest_backup_file(backup_dir: str | Path = DEFAULT_BACKUP_DIR) -> Path | None:
    """
    Lấy file backup mới nhất trong data/backup.
    Hỗ trợ .sql, .sql.gz, .dump, .dump.gz.
    """

    backup_dir = resolve_project_path(backup_dir)

    if not backup_dir.exists():
        return None

    files = []
    files.extend(backup_dir.glob("*.sql"))
    files.extend(backup_dir.glob("*.sql.gz"))
    files.extend(backup_dir.glob("*.dump"))
    files.extend(backup_dir.glob("*.dump.gz"))

    if not files:
        return None

    return max(files, key=lambda p: p.stat().st_mtime)


# ============================================================
# GZIP HELPERS
# ============================================================

def gzip_file(file_path: str | Path) -> Path:
    """
    Nén file .sql hoặc .dump thành .gz.
    """

    file_path = resolve_project_path(file_path)
    gzip_path = file_path.with_suffix(file_path.suffix + ".gz")

    logger.info("Compressing backup file: %s", gzip_path)

    with open(file_path, "rb") as f_in:
        with gzip.open(gzip_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    file_path.unlink()

    return gzip_path


def prepare_restore_file(input_path: str | Path) -> tuple[Path, bool]:
    """
    Chuẩn bị file để restore.

    Nếu input là .gz:
        giải nén tạm vào data/backup/restore_db/.restore_tmp/
        trả về file đã giải nén và is_temp=True

    Nếu input không nén:
        trả về chính file đó và is_temp=False
    """

    input_path = validate_restore_file(input_path)

    if input_path.name.lower().endswith(".gz"):
        tmp_dir = ensure_dir("data/backup/restore_db/.restore_tmp")
        tmp_filename = input_path.name[:-3]
        tmp_path = tmp_dir / tmp_filename

        logger.info("Decompressing restore file: %s -> %s", input_path, tmp_path)

        with gzip.open(input_path, "rb") as f_in:
            with open(tmp_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        return tmp_path, True

    return input_path, False


def cleanup_temp_file(file_path: str | Path) -> None:
    """
    Xóa file tạm.
    """

    file_path = resolve_project_path(file_path)

    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        logger.info("Removed temporary file: %s", file_path)


# ============================================================
# SQL RUNNERS
# ============================================================

def run_sql_docker(
    sql: str,
    container_name: str = POSTGRES_CONTAINER,
    database: str = DB_NAME,
    user: str = DB_USER,
) -> None:
    """
    Chạy SQL ngắn trong Docker container.
    """

    run_command(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        "Run SQL inside PostgreSQL container",
    )


def run_sql_local(
    sql: str,
    database: str = DB_NAME,
    user: str = DB_USER,
    password: str = DB_PASSWORD,
    host: str = DB_HOST,
    port: str = DB_PORT,
) -> None:
    """
    Chạy SQL ngắn bằng psql local.
    """

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    run_command(
        [
            "psql",
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        "Run SQL by local psql",
        env=env,
    )


# ============================================================
# BACKUP - DOCKER
# ============================================================

def backup_database_docker(
    output_path: str | Path | None = None,
    container_name: str = POSTGRES_CONTAINER,
    database: str = DB_NAME,
    user: str = DB_USER,
    schema: str | None = DB_SCHEMA,
    with_timestamp: bool = False,
    clean: bool = False,
    gzip_output: bool = False,
    backup_format: str = "sql",
) -> Path:
    """
    Backup database bằng pg_dump trong Docker.

    backup_format:
        sql  -> plain SQL, restore bằng psql
        dump -> custom format -Fc, restore bằng pg_restore
    """

    check_docker_available()
    check_container_running(container_name)

    output_path, backup_format, gzip_output = normalize_backup_output_path(
        output_path=output_path,
        with_timestamp=with_timestamp,
        backup_format=backup_format,
        gzip_output=gzip_output,
    )

    container_tmp_file = f"/tmp/{Path(output_path).name}"

    command = [
        "docker",
        "exec",
        container_name,
        "pg_dump",
        "-U",
        user,
        "-d",
        database,
        "--encoding=UTF8",
        "--no-owner",
        "--no-privileges",
        "-f",
        container_tmp_file,
    ]

    if backup_format == "dump":
        command.extend(["-Fc"])

    if schema:
        command.extend(["--schema", schema])

    if clean and backup_format == "sql":
        command.extend(["--clean", "--if-exists"])

    if clean and backup_format == "dump":
        logger.warning(
            "--clean is ignored when creating .dump. "
            "Use restore --clean when restoring .dump."
        )

    try:
        run_command(
            command,
            f"Create {backup_format} backup file inside PostgreSQL container",
        )

        run_command(
            [
                "docker",
                "cp",
                f"{container_name}:{container_tmp_file}",
                str(output_path),
            ],
            f"Copy backup file to host: {output_path}",
        )

    finally:
        try:
            run_command(
                [
                    "docker",
                    "exec",
                    container_name,
                    "rm",
                    "-f",
                    container_tmp_file,
                ],
                "Remove temporary backup file inside container",
            )
        except Exception as e:
            logger.warning("Cannot remove temporary backup file in container: %s", e)

    if gzip_output:
        output_path = gzip_file(output_path)

    logger.info("Backup completed: %s", output_path)
    logger.info("Backup size: %.2f MB", output_path.stat().st_size / 1024 / 1024)

    return output_path


# ============================================================
# BACKUP - LOCAL
# ============================================================

def backup_database_local(
    output_path: str | Path | None = None,
    database: str = DB_NAME,
    user: str = DB_USER,
    password: str = DB_PASSWORD,
    host: str = DB_HOST,
    port: str = DB_PORT,
    schema: str | None = DB_SCHEMA,
    with_timestamp: bool = False,
    clean: bool = False,
    gzip_output: bool = False,
    backup_format: str = "sql",
) -> Path:
    """
    Backup database bằng pg_dump local.
    """

    output_path, backup_format, gzip_output = normalize_backup_output_path(
        output_path=output_path,
        with_timestamp=with_timestamp,
        backup_format=backup_format,
        gzip_output=gzip_output,
    )

    command = [
        "pg_dump",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        database,
        "--encoding=UTF8",
        "--no-owner",
        "--no-privileges",
        "-f",
        str(output_path),
    ]

    if backup_format == "dump":
        command.extend(["-Fc"])

    if schema:
        command.extend(["--schema", schema])

    if clean and backup_format == "sql":
        command.extend(["--clean", "--if-exists"])

    if clean and backup_format == "dump":
        logger.warning(
            "--clean is ignored when creating .dump. "
            "Use restore --clean when restoring .dump."
        )

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    run_command(
        command,
        f"Create local {backup_format} backup",
        env=env,
    )

    if gzip_output:
        output_path = gzip_file(output_path)

    logger.info("Backup completed: %s", output_path)
    logger.info("Backup size: %.2f MB", output_path.stat().st_size / 1024 / 1024)

    return output_path


# ============================================================
# RESTORE - DOCKER
# ============================================================

def restore_database_docker(
    input_path: str | Path,
    container_name: str = POSTGRES_CONTAINER,
    database: str = DB_NAME,
    user: str = DB_USER,
    schema: str | None = DB_SCHEMA,
    drop_schema_first: bool = False,
    restore_clean: bool = False,
) -> None:
    """
    Restore database bằng Docker.

    .sql  -> psql -f
    .dump -> pg_restore
    """

    check_docker_available()
    check_container_running(container_name)

    restore_file, is_temp = prepare_restore_file(input_path)
    backup_format = detect_backup_format(restore_file)

    container_tmp_file = f"/tmp/{restore_file.name}"

    try:
        if drop_schema_first and schema:
            logger.warning("Dropping schema before restore: %s", schema)

            run_sql_docker(
                sql=f'DROP SCHEMA IF EXISTS "{schema}" CASCADE;',
                container_name=container_name,
                database=database,
                user=user,
            )

        run_command(
            [
                "docker",
                "cp",
                str(restore_file),
                f"{container_name}:{container_tmp_file}",
            ],
            f"Copy restore file to container: {container_tmp_file}",
        )

        if backup_format == "sql":
            command = [
                "docker",
                "exec",
                container_name,
                "psql",
                "-U",
                user,
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                container_tmp_file,
            ]

        else:
            command = [
                "docker",
                "exec",
                container_name,
                "pg_restore",
                "-U",
                user,
                "-d",
                database,
                "--no-owner",
                "--no-privileges",
                "-v",
            ]

            if restore_clean:
                command.extend(["--clean", "--if-exists"])

            command.append(container_tmp_file)

        run_command(
            command,
            f"Restore database from {backup_format} file inside PostgreSQL container",
        )

        logger.info("Restore completed successfully from: %s", input_path)

    finally:
        try:
            run_command(
                [
                    "docker",
                    "exec",
                    container_name,
                    "rm",
                    "-f",
                    container_tmp_file,
                ],
                "Remove temporary restore file inside container",
            )
        except Exception as e:
            logger.warning("Cannot remove temporary restore file in container: %s", e)

        if is_temp:
            cleanup_temp_file(restore_file)


# ============================================================
# RESTORE - LOCAL
# ============================================================

def restore_database_local(
    input_path: str | Path,
    database: str = DB_NAME,
    user: str = DB_USER,
    password: str = DB_PASSWORD,
    host: str = DB_HOST,
    port: str = DB_PORT,
    schema: str | None = DB_SCHEMA,
    drop_schema_first: bool = False,
    restore_clean: bool = False,
) -> None:
    """
    Restore database local.

    .sql  -> psql -f
    .dump -> pg_restore
    """

    restore_file, is_temp = prepare_restore_file(input_path)
    backup_format = detect_backup_format(restore_file)

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    try:
        if drop_schema_first and schema:
            logger.warning("Dropping schema before restore: %s", schema)

            run_sql_local(
                sql=f'DROP SCHEMA IF EXISTS "{schema}" CASCADE;',
                database=database,
                user=user,
                password=password,
                host=host,
                port=port,
            )

        if backup_format == "sql":
            command = [
                "psql",
                "-h",
                host,
                "-p",
                str(port),
                "-U",
                user,
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                str(restore_file),
            ]

        else:
            command = [
                "pg_restore",
                "-h",
                host,
                "-p",
                str(port),
                "-U",
                user,
                "-d",
                database,
                "--no-owner",
                "--no-privileges",
                "-v",
            ]

            if restore_clean:
                command.extend(["--clean", "--if-exists"])

            command.append(str(restore_file))

        run_command(
            command,
            f"Restore database by local {backup_format} restore",
            env=env,
        )

        logger.info("Restore completed successfully from: %s", input_path)

    finally:
        if is_temp:
            cleanup_temp_file(restore_file)


# ============================================================
# PUBLIC FUNCTIONS
# ============================================================

def backup_database(
    mode: str = "docker",
    output_path: str | Path | None = None,
    with_timestamp: bool = False,
    clean: bool = False,
    gzip_output: bool = False,
    schema: str | None = DB_SCHEMA,
    container_name: str = POSTGRES_CONTAINER,
    backup_format: str = "sql",
) -> Path:
    """
    Hàm backup chính để module khác import dùng lại.
    """

    if mode == "docker":
        return backup_database_docker(
            output_path=output_path,
            container_name=container_name,
            schema=schema,
            with_timestamp=with_timestamp,
            clean=clean,
            gzip_output=gzip_output,
            backup_format=backup_format,
        )

    if mode == "local":
        return backup_database_local(
            output_path=output_path,
            schema=schema,
            with_timestamp=with_timestamp,
            clean=clean,
            gzip_output=gzip_output,
            backup_format=backup_format,
        )

    raise ValueError("mode must be 'docker' or 'local'")


def restore_database(
    input_path: str | Path,
    mode: str = "docker",
    drop_schema_first: bool = False,
    schema: str | None = DB_SCHEMA,
    container_name: str = POSTGRES_CONTAINER,
    restore_clean: bool = False,
) -> None:
    """
    Hàm restore chính để module khác import dùng lại.
    """

    if mode == "docker":
        restore_database_docker(
            input_path=input_path,
            container_name=container_name,
            schema=schema,
            drop_schema_first=drop_schema_first,
            restore_clean=restore_clean,
        )
        return

    if mode == "local":
        restore_database_local(
            input_path=input_path,
            schema=schema,
            drop_schema_first=drop_schema_first,
            restore_clean=restore_clean,
        )
        return

    raise ValueError("mode must be 'docker' or 'local'")


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    """
    CLI hỗ trợ:

        py -m src.database.backup
        py -m src.database.backup backup
        py -m src.database.backup restore --input ...
    """

    argv = sys.argv[1:]

    # Backward compatible:
    # py -m src.database.backup
    # py -m src.database.backup --timestamp --clean
    if not argv or argv[0].startswith("-"):
        argv = ["backup"] + argv

    parser = argparse.ArgumentParser(
        description="Backup / Restore PostgreSQL database for TNBIKE project"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # ----------------------------
    # backup command
    # ----------------------------
    backup_parser = subparsers.add_parser(
        "backup",
        help="Backup PostgreSQL database",
    )

    backup_parser.add_argument(
        "--mode",
        choices=["docker", "local"],
        default="docker",
        help="Backup mode. Default: docker",
    )

    backup_parser.add_argument(
        "--format",
        choices=["sql", "dump"],
        default="sql",
        help="Backup format. sql = plain SQL, dump = custom pg_dump format. Default: sql",
    )

    backup_parser.add_argument(
        "--output",
        default=None,
        help="Output backup file path. Example: data/backup/restore_db/tnbike_db_backup.dump",
    )

    backup_parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Add timestamp to backup filename",
    )

    backup_parser.add_argument(
        "--clean",
        action="store_true",
        help="Add DROP statements with --clean --if-exists. Only applies to .sql backup.",
    )

    backup_parser.add_argument(
        "--gzip",
        action="store_true",
        help="Compress backup file to .gz",
    )

    backup_parser.add_argument(
        "--schema",
        default=DB_SCHEMA,
        help="Schema to backup. Use empty string to backup full database.",
    )

    backup_parser.add_argument(
        "--container",
        default=POSTGRES_CONTAINER,
        help="Docker container name. Default: tnbike_postgres",
    )

    # ----------------------------
    # restore command
    # ----------------------------
    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore PostgreSQL database",
    )

    restore_parser.add_argument(
        "--mode",
        choices=["docker", "local"],
        default="docker",
        help="Restore mode. Default: docker",
    )

    restore_parser.add_argument(
        "--input",
        required=False,
        default=None,
        help="Input backup file path. If omitted, use latest file in data/backup.",
    )

    restore_parser.add_argument(
        "--latest",
        action="store_true",
        help="Restore latest backup file from data/backup.",
    )

    restore_parser.add_argument(
        "--drop-schema-first",
        action="store_true",
        help="DROP schema before restore. Be careful: this deletes current schema data.",
    )

    restore_parser.add_argument(
        "--clean",
        action="store_true",
        help="Use pg_restore --clean --if-exists when restoring .dump.",
    )

    restore_parser.add_argument(
        "--schema",
        default=DB_SCHEMA,
        help="Schema to drop before restore. Use empty string to disable schema drop.",
    )

    restore_parser.add_argument(
        "--container",
        default=POSTGRES_CONTAINER,
        help="Docker container name. Default: tnbike_postgres",
    )

    return parser.parse_args(argv)


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="backup_database.log",
        error_log_file="error.log",
    )

    args = parse_args()

    if args.command == "backup":
        schema = args.schema if args.schema else None

        logger.info("=" * 70)
        logger.info("TNBIKE DATABASE BACKUP STARTED")
        logger.info("=" * 70)
        logger.info("Mode      : %s", args.mode)
        logger.info("Format    : %s", args.format)
        logger.info("Database  : %s", DB_NAME)
        logger.info("Schema    : %s", schema if schema else "FULL DATABASE")
        logger.info("Container : %s", args.container)
        logger.info("Output    : %s", args.output if args.output else "default")
        logger.info("Timestamp : %s", args.timestamp)
        logger.info("Clean     : %s", args.clean)
        logger.info("Gzip      : %s", args.gzip)
        logger.info("=" * 70)

        try:
            backup_path = backup_database(
                mode=args.mode,
                output_path=args.output,
                with_timestamp=args.timestamp,
                clean=args.clean,
                gzip_output=args.gzip,
                schema=schema,
                container_name=args.container,
                backup_format=args.format,
            )

            logger.info("=" * 70)
            logger.info("BACKUP SUCCESS")
            logger.info("File: %s", backup_path)
            logger.info("=" * 70)

        except Exception as e:
            logger.exception("BACKUP FAILED: %s", e)
            sys.exit(1)

    elif args.command == "restore":
        schema = args.schema if args.schema else None

        input_path = args.input

        if args.latest or input_path is None:
            latest_file = get_latest_backup_file()

            if latest_file is None:
                logger.error("No backup file found in data/backup")
                sys.exit(1)

            input_path = latest_file

        logger.info("=" * 70)
        logger.info("TNBIKE DATABASE RESTORE STARTED")
        logger.info("=" * 70)
        logger.info("Mode              : %s", args.mode)
        logger.info("Database          : %s", DB_NAME)
        logger.info("Input             : %s", input_path)
        logger.info("Schema            : %s", schema if schema else "N/A")
        logger.info("Drop schema first : %s", args.drop_schema_first)
        logger.info("Clean             : %s", args.clean)
        logger.info("Container         : %s", args.container)
        logger.info("=" * 70)

        try:
            restore_database(
                input_path=input_path,
                mode=args.mode,
                drop_schema_first=args.drop_schema_first,
                schema=schema,
                container_name=args.container,
                restore_clean=args.clean,
            )

            logger.info("=" * 70)
            logger.info("RESTORE SUCCESS")
            logger.info("File: %s", input_path)
            logger.info("=" * 70)

        except Exception as e:
            logger.exception("RESTORE FAILED: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()