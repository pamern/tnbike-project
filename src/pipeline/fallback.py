    # ============================================================
# src/pipeline/fallback.py
# Pipeline fallback utilities for TNBIKE
#
# Chức năng:
#   1. Tạo restore point DB trước khi chạy pipeline
#   2. Restore DB về trạng thái trước pipeline
#   3. Move file .eml từ processed_pipeline về incoming/eml
# ============================================================

import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime


# ============================================================
# IMPORT PROJECT MODULES
# ============================================================

try:
    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger
    from src.database.connection import DB_SCHEMA
    from src.database.backup import (
        backup_database,
        restore_database,
        POSTGRES_CONTAINER,
    )

except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT))

    from src.utils.file_utils import ensure_dir, resolve_project_path
    from src.config.logging_config import setup_logging, get_logger
    from src.database.connection import DB_SCHEMA
    from src.database.backup import (
        backup_database,
        restore_database,
        POSTGRES_CONTAINER,
    )


logger = get_logger(__name__)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_FALLBACK_DIR = "data/backup/pipeline_restore_point"
DEFAULT_RESTORE_POINT_FILE = "pre_pipeline_state.dump"

DEFAULT_INCOMING_EML_DIR = "data/incoming/eml"

DEFAULT_PROCESSED_EML_DIRS = [
    "data/processed/success_eml/eml",
    "data/processed/failed_eml/eml",
]


# ============================================================
# DB RESTORE POINT
# ============================================================

def get_restore_point_path(
    fallback_dir: str | Path = DEFAULT_FALLBACK_DIR,
    filename: str = DEFAULT_RESTORE_POINT_FILE,
) -> Path:
    """
    Lấy đường dẫn restore point mặc định.
    """

    fallback_dir = ensure_dir(fallback_dir)
    return fallback_dir / filename


def create_pipeline_restore_point(
    output_path: str | Path | None = None,
    mode: str = "docker",
    backup_format: str = "dump",
    schema: str | None = DB_SCHEMA,
    container_name: str = POSTGRES_CONTAINER,
) -> Path:
    """
    Tạo backup DB trước khi chạy pipeline.

    Mặc định ghi đè:
        data/backup/pipeline_restore_point/pre_pipeline_state.dump
    """

    if output_path is None:
        output_path = get_restore_point_path()
    else:
        output_path = resolve_project_path(output_path)

    logger.info("=" * 70)
    logger.info("CREATE PIPELINE RESTORE POINT STARTED")
    logger.info("=" * 70)
    logger.info("Output    : %s", output_path)
    logger.info("Mode      : %s", mode)
    logger.info("Format    : %s", backup_format)
    logger.info("Schema    : %s", schema)
    logger.info("Container : %s", container_name)
    logger.info("=" * 70)

    backup_path = backup_database(
        mode=mode,
        output_path=output_path,
        with_timestamp=False,
        clean=False,
        gzip_output=False,
        schema=schema,
        container_name=container_name,
        backup_format=backup_format,
    )

    logger.info("=" * 70)
    logger.info("CREATE PIPELINE RESTORE POINT SUCCESS")
    logger.info("Restore point: %s", backup_path)
    logger.info("=" * 70)

    return backup_path


def create_timestamped_pipeline_restore_point(
    fallback_dir: str | Path = DEFAULT_FALLBACK_DIR,
    mode: str = "docker",
    backup_format: str = "dump",
    schema: str | None = DB_SCHEMA,
    container_name: str = POSTGRES_CONTAINER,
) -> Path:
    """
    Tạo restore point có timestamp để giữ nhiều mốc test.
    """

    fallback_dir = ensure_dir(fallback_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = fallback_dir / f"pre_pipeline_state_{timestamp}.{backup_format}"

    return create_pipeline_restore_point(
        output_path=output_path,
        mode=mode,
        backup_format=backup_format,
        schema=schema,
        container_name=container_name,
    )


def restore_pipeline_restore_point(
    input_path: str | Path | None = None,
    mode: str = "docker",
    drop_schema_first: bool = True,
    restore_clean: bool = False,
    schema: str | None = DB_SCHEMA,
    container_name: str = POSTGRES_CONTAINER,
    dry_run: bool = False,
) -> Path:
    """
    Restore DB về trạng thái trước khi chạy pipeline.
    """

    if input_path is None:
        input_path = get_restore_point_path()
    else:
        input_path = resolve_project_path(input_path)

    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Restore point not found: {input_path}. "
            f"Create it first: py -m src.pipeline.fallback create"
        )

    logger.info("=" * 70)
    logger.info("RESTORE PIPELINE RESTORE POINT STARTED")
    logger.info("=" * 70)
    logger.info("Input             : %s", input_path)
    logger.info("Mode              : %s", mode)
    logger.info("Drop schema first : %s", drop_schema_first)
    logger.info("Restore clean     : %s", restore_clean)
    logger.info("Schema            : %s", schema)
    logger.info("Container         : %s", container_name)
    logger.info("Dry run           : %s", dry_run)
    logger.info("=" * 70)

    if dry_run:
        logger.info("[DRY RUN] Would restore DB from: %s", input_path)
        return input_path

    restore_database(
        input_path=input_path,
        mode=mode,
        drop_schema_first=drop_schema_first,
        schema=schema,
        container_name=container_name,
        restore_clean=restore_clean,
    )

    logger.info("=" * 70)
    logger.info("RESTORE PIPELINE RESTORE POINT SUCCESS")
    logger.info("Restored from: %s", input_path)
    logger.info("=" * 70)

    return input_path


# ============================================================
# FILE FALLBACK
# ============================================================

def build_unique_target_path(target_path: Path) -> Path:
    """
    Nếu file đích đã tồn tại thì tạo tên mới để không ghi đè.
    """

    if not target_path.exists():
        return target_path

    parent = target_path.parent
    stem = target_path.stem
    suffix = target_path.suffix

    counter = 1

    while True:
        candidate = parent / f"{stem}__fallback_{counter:03d}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def move_single_file_back(
    source_file: Path,
    target_dir: Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Move 1 file .eml về incoming.
    """

    source_file = Path(source_file)
    target_dir = ensure_dir(target_dir)

    target_path = target_dir / source_file.name

    if target_path.exists() and not overwrite:
        target_path = build_unique_target_path(target_path)

    result = {
        "source": str(source_file),
        "target": str(target_path),
        "status": "DRY_RUN" if dry_run else "MOVED",
        "error": "",
    }

    if dry_run:
        logger.info("[DRY RUN] Move: %s -> %s", source_file, target_path)
        return result

    try:
        if overwrite and target_path.exists():
            target_path.unlink()

        shutil.move(str(source_file), str(target_path))

        logger.info("Moved file back: %s -> %s", source_file, target_path)

    except Exception as e:
        logger.exception("Failed to move file: %s", source_file)

        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


def move_processed_files_back(
    source_dirs: list[str | Path] | None = None,
    target_dir: str | Path = DEFAULT_INCOMING_EML_DIR,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Move toàn bộ .eml từ processed_pipeline về incoming/eml.
    """

    if source_dirs is None:
        source_dirs = DEFAULT_PROCESSED_EML_DIRS

    target_dir = ensure_dir(target_dir)

    logger.info("=" * 70)
    logger.info("MOVE PROCESSED FILES BACK STARTED")
    logger.info("=" * 70)
    logger.info("Target dir : %s", target_dir)
    logger.info("Overwrite  : %s", overwrite)
    logger.info("Dry run    : %s", dry_run)
    logger.info("=" * 70)

    results = []
    skipped_dirs = []

    for source_dir in source_dirs:
        source_dir = resolve_project_path(source_dir)

        if not source_dir.exists():
            logger.warning("Source dir does not exist, skipped: %s", source_dir)
            skipped_dirs.append(str(source_dir))
            continue

        eml_files = sorted(source_dir.glob("*.eml"))

        logger.info("Source dir: %s | Found .eml: %s", source_dir, len(eml_files))

        for eml_file in eml_files:
            result = move_single_file_back(
                source_file=eml_file,
                target_dir=target_dir,
                overwrite=overwrite,
                dry_run=dry_run,
            )

            results.append(result)

    status_counts = {}

    for row in results:
        status = row["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    summary = {
        "total_files": len(results),
        "status_counts": status_counts,
        "skipped_dirs": skipped_dirs,
        "results": results,
    }

    logger.info("=" * 70)
    logger.info("MOVE PROCESSED FILES BACK FINISHED")
    logger.info("Total files   : %s", summary["total_files"])
    logger.info("Status counts : %s", summary["status_counts"])
    logger.info("Skipped dirs  : %s", summary["skipped_dirs"])
    logger.info("=" * 70)

    return summary


# ============================================================
# FULL PIPELINE FALLBACK
# ============================================================

def rollback_pipeline_test(
    restore_db: bool = True,
    move_files: bool = True,
    restore_point: str | Path | None = None,
    mode: str = "docker",
    drop_schema_first: bool = True,
    restore_clean: bool = False,
    schema: str | None = DB_SCHEMA,
    container_name: str = POSTGRES_CONTAINER,
    source_dirs: list[str | Path] | None = None,
    target_dir: str | Path = DEFAULT_INCOMING_EML_DIR,
    overwrite_files: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Rollback sau khi test pipeline.

    Thứ tự:
        1. Restore DB về restore point trước pipeline
        2. Move file .eml từ processed_pipeline về incoming/eml

    Lý do:
        Nếu restore DB lỗi thì chưa đụng file.
    """

    result = {
        "database": None,
        "files": None,
    }

    if restore_db:
        result["database"] = {
            "restored_from": str(
                restore_pipeline_restore_point(
                    input_path=restore_point,
                    mode=mode,
                    drop_schema_first=drop_schema_first,
                    restore_clean=restore_clean,
                    schema=schema,
                    container_name=container_name,
                    dry_run=dry_run,
                )
            )
        }

    if move_files:
        result["files"] = move_processed_files_back(
            source_dirs=source_dirs,
            target_dir=target_dir,
            overwrite=overwrite_files,
            dry_run=dry_run,
        )

    return result


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline fallback: DB restore point + move processed files back"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # --------------------------------------------------------
    # create
    # --------------------------------------------------------
    create_parser = subparsers.add_parser(
        "create",
        help="Create DB restore point before running pipeline",
    )

    create_parser.add_argument(
        "--output",
        default=None,
        help="Output restore point file. Default: data/backup/pipeline_restore_point/pre_pipeline_state.dump",
    )

    create_parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Create timestamped restore point",
    )

    create_parser.add_argument(
        "--mode",
        choices=["docker", "local"],
        default="docker",
    )

    create_parser.add_argument(
        "--format",
        choices=["sql", "dump"],
        default="dump",
    )

    create_parser.add_argument(
        "--schema",
        default=DB_SCHEMA,
        help="Schema to backup. Use empty string for full database.",
    )

    create_parser.add_argument(
        "--container",
        default=POSTGRES_CONTAINER,
    )

    # --------------------------------------------------------
    # restore-db
    # --------------------------------------------------------
    restore_parser = subparsers.add_parser(
        "restore-db",
        help="Restore DB to pre-pipeline restore point",
    )

    restore_parser.add_argument(
        "--input",
        default=None,
        help="Restore point file. Default: pre_pipeline_state.dump",
    )

    restore_parser.add_argument(
        "--mode",
        choices=["docker", "local"],
        default="docker",
    )

    restore_parser.add_argument(
        "--no-drop-schema-first",
        action="store_true",
    )

    restore_parser.add_argument(
        "--clean",
        action="store_true",
        help="Use pg_restore --clean --if-exists for .dump restore",
    )

    restore_parser.add_argument(
        "--schema",
        default=DB_SCHEMA,
    )

    restore_parser.add_argument(
        "--container",
        default=POSTGRES_CONTAINER,
    )

    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    # --------------------------------------------------------
    # move-files
    # --------------------------------------------------------
    move_parser = subparsers.add_parser(
        "move-files",
        help="Move processed .eml files back to incoming/eml",
    )

    move_parser.add_argument(
        "--source-dir",
        action="append",
        default=None,
        help="Source processed folder. Can be used multiple times.",
    )

    move_parser.add_argument(
        "--target-dir",
        default=DEFAULT_INCOMING_EML_DIR,
    )

    move_parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    move_parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    # --------------------------------------------------------
    # rollback
    # --------------------------------------------------------
    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Restore DB and move processed files back",
    )

    rollback_parser.add_argument(
        "--restore-point",
        default=None,
        help="Restore point file. Default: pre_pipeline_state.dump",
    )

    rollback_parser.add_argument(
        "--mode",
        choices=["docker", "local"],
        default="docker",
    )

    rollback_parser.add_argument(
        "--no-drop-schema-first",
        action="store_true",
    )

    rollback_parser.add_argument(
        "--clean",
        action="store_true",
    )

    rollback_parser.add_argument(
        "--schema",
        default=DB_SCHEMA,
    )

    rollback_parser.add_argument(
        "--container",
        default=POSTGRES_CONTAINER,
    )

    rollback_parser.add_argument(
        "--source-dir",
        action="append",
        default=None,
    )

    rollback_parser.add_argument(
        "--target-dir",
        default=DEFAULT_INCOMING_EML_DIR,
    )

    rollback_parser.add_argument(
        "--overwrite-files",
        action="store_true",
    )

    rollback_parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging(
        log_level="INFO",
        pipeline_log_file="fallback.log",
        error_log_file="error.log",
    )

    args = parse_args()

    try:
        if args.command == "create":
            schema = args.schema if args.schema else None

            if args.timestamp:
                restore_point = create_timestamped_pipeline_restore_point(
                    mode=args.mode,
                    backup_format=args.format,
                    schema=schema,
                    container_name=args.container,
                )
            else:
                restore_point = create_pipeline_restore_point(
                    output_path=args.output,
                    mode=args.mode,
                    backup_format=args.format,
                    schema=schema,
                    container_name=args.container,
                )

            print("")
            print("CREATE RESTORE POINT SUCCESS")
            print(f"Restore point: {restore_point}")

        elif args.command == "restore-db":
            schema = args.schema if args.schema else None

            restored_from = restore_pipeline_restore_point(
                input_path=args.input,
                mode=args.mode,
                drop_schema_first=not args.no_drop_schema_first,
                restore_clean=args.clean,
                schema=schema,
                container_name=args.container,
                dry_run=args.dry_run,
            )

            print("")
            print("RESTORE DB SUCCESS")
            print(f"Restored from: {restored_from}")

        elif args.command == "move-files":
            summary = move_processed_files_back(
                source_dirs=args.source_dir,
                target_dir=args.target_dir,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )

            print("")
            print("MOVE FILES SUCCESS")
            print(f"Total files   : {summary['total_files']}")
            print(f"Status counts : {summary['status_counts']}")

        elif args.command == "rollback":
            schema = args.schema if args.schema else None

            result = rollback_pipeline_test(
                restore_db=True,
                move_files=True,
                restore_point=args.restore_point,
                mode=args.mode,
                drop_schema_first=not args.no_drop_schema_first,
                restore_clean=args.clean,
                schema=schema,
                container_name=args.container,
                source_dirs=args.source_dir,
                target_dir=args.target_dir,
                overwrite_files=args.overwrite_files,
                dry_run=args.dry_run,
            )

            print("")
            print("PIPELINE ROLLBACK SUCCESS")

            if result.get("database"):
                print(f"DB restored from : {result['database']['restored_from']}")

            if result.get("files"):
                print(f"Files total      : {result['files']['total_files']}")
                print(f"File statuses    : {result['files']['status_counts']}")

    except Exception as e:
        logger.exception("FALLBACK FAILED: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()