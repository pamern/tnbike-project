# ============================================================
# src/utils/file_utils.py
# Tiện ích xử lý file/folder cho TNBIKE Pipeline
# ============================================================

import os
import re
import json
import shutil
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Iterable, Optional


logger = logging.getLogger(__name__)


# ============================================================
# PATH HELPERS
# ============================================================

def get_project_root() -> Path:
    """
    Trả về thư mục gốc project.
    Giả định file này nằm tại src/utils/file_utils.py
    """
    return Path(__file__).resolve().parents[2]


def to_path(path: str | Path) -> Path:
    """
    Ép path về pathlib.Path.
    """
    return path if isinstance(path, Path) else Path(path)


def resolve_project_path(path: str | Path) -> Path:
    """
    Chuyển path tương đối theo project root thành path tuyệt đối.
    """
    path = to_path(path)

    if path.is_absolute():
        return path

    return get_project_root() / path


def ensure_dir(path: str | Path) -> Path:
    """
    Tạo folder nếu chưa tồn tại.
    """
    path = resolve_project_path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_parent_dir(file_path: str | Path) -> Path:
    """
    Tạo folder cha của một file nếu chưa tồn tại.
    """
    file_path = resolve_project_path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    return file_path


# ============================================================
# LIST / FIND FILES
# ============================================================

def list_files(
    folder: str | Path,
    extensions: Optional[Iterable[str]] = None,
    recursive: bool = False,
) -> list[Path]:
    """
    Liệt kê file trong folder.

    Args:
        folder: thư mục cần scan
        extensions: ví dụ [".eml", ".pdf"]
        recursive: True nếu muốn quét cả thư mục con
    """
    folder = resolve_project_path(folder)

    if not folder.exists():
        logger.warning("Folder does not exist: %s", folder)
        return []

    if not folder.is_dir():
        logger.warning("Path is not a folder: %s", folder)
        return []

    if extensions:
        extensions = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}

    pattern = "**/*" if recursive else "*"

    files = [
        p for p in folder.glob(pattern)
        if p.is_file()
        and (extensions is None or p.suffix.lower() in extensions)
    ]

    return sorted(files)


def list_eml_files(folder: str | Path, recursive: bool = False) -> list[Path]:
    """
    Liệt kê file .eml.
    """
    return list_files(folder, extensions=[".eml"], recursive=recursive)


def list_pdf_files(folder: str | Path, recursive: bool = False) -> list[Path]:
    """
    Liệt kê file .pdf.
    """
    return list_files(folder, extensions=[".pdf"], recursive=recursive)


def file_exists(path: str | Path) -> bool:
    """
    Kiểm tra file có tồn tại không.
    """
    path = resolve_project_path(path)
    return path.exists() and path.is_file()


def folder_exists(path: str | Path) -> bool:
    """
    Kiểm tra folder có tồn tại không.
    """
    path = resolve_project_path(path)
    return path.exists() and path.is_dir()


# ============================================================
# FILE NAME HELPERS
# ============================================================

def safe_filename(filename: str) -> str:
    """
    Làm sạch tên file để tránh ký tự lỗi trên Windows.
    """
    filename = filename.strip()
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    filename = re.sub(r"\s+", " ", filename)
    return filename


def timestamp_str(fmt: str = "%Y%m%d_%H%M%S") -> str:
    """
    Tạo timestamp string.
    """
    return datetime.now().strftime(fmt)


def add_timestamp_to_filename(file_path: str | Path) -> Path:
    """
    Thêm timestamp vào tên file.

    Ví dụ:
        staging_order_line.csv
        -> staging_order_line_20260519_153000.csv
    """
    file_path = to_path(file_path)
    ts = timestamp_str()

    return file_path.with_name(f"{file_path.stem}_{ts}{file_path.suffix}")


def make_unique_path(file_path: str | Path) -> Path:
    """
    Nếu file đã tồn tại thì tự thêm _001, _002, ...
    """
    file_path = resolve_project_path(file_path)

    if not file_path.exists():
        return file_path

    parent = file_path.parent
    stem = file_path.stem
    suffix = file_path.suffix

    counter = 1

    while True:
        candidate = parent / f"{stem}_{counter:03d}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


# ============================================================
# MOVE / COPY / DELETE
# ============================================================

def copy_file(
    src: str | Path,
    dst: str | Path,
    overwrite: bool = False,
) -> Path:
    """
    Copy file từ src sang dst.
    """
    src = resolve_project_path(src)
    dst = resolve_project_path(dst)

    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")

    ensure_parent_dir(dst)

    if dst.exists() and not overwrite:
        dst = make_unique_path(dst)

    shutil.copy2(src, dst)

    logger.info("Copied file: %s -> %s", src, dst)
    return dst


def move_file(
    src: str | Path,
    dst: str | Path,
    overwrite: bool = False,
) -> Path:
    """
    Move file từ src sang dst.
    """
    src = resolve_project_path(src)
    dst = resolve_project_path(dst)

    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")

    ensure_parent_dir(dst)

    if dst.exists() and not overwrite:
        dst = make_unique_path(dst)

    shutil.move(str(src), str(dst))

    logger.info("Moved file: %s -> %s", src, dst)
    return dst


def move_to_folder(
    src: str | Path,
    dst_folder: str | Path,
    overwrite: bool = False,
) -> Path:
    """
    Move file vào một folder, giữ nguyên tên file.
    """
    src = resolve_project_path(src)
    dst_folder = ensure_dir(dst_folder)

    dst = dst_folder / src.name

    return move_file(src, dst, overwrite=overwrite)


def copy_to_folder(
    src: str | Path,
    dst_folder: str | Path,
    overwrite: bool = False,
) -> Path:
    """
    Copy file vào một folder, giữ nguyên tên file.
    """
    src = resolve_project_path(src)
    dst_folder = ensure_dir(dst_folder)

    dst = dst_folder / src.name

    return copy_file(src, dst, overwrite=overwrite)


def delete_file(path: str | Path, missing_ok: bool = True) -> None:
    """
    Xóa file.
    """
    path = resolve_project_path(path)

    if not path.exists():
        if missing_ok:
            return
        raise FileNotFoundError(f"File not found: {path}")

    if path.is_file():
        path.unlink()
        logger.info("Deleted file: %s", path)


# ============================================================
# HASH / DUPLICATE CHECK
# ============================================================

def get_file_hash(path: str | Path, algorithm: str = "md5") -> str:
    """
    Tính hash file để kiểm tra trùng lặp.
    """
    path = resolve_project_path(path)

    if algorithm.lower() == "md5":
        hash_obj = hashlib.md5()
    elif algorithm.lower() == "sha256":
        hash_obj = hashlib.sha256()
    else:
        raise ValueError("algorithm must be 'md5' or 'sha256'")

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)

    return hash_obj.hexdigest()


def get_file_size(path: str | Path) -> int:
    """
    Lấy dung lượng file theo byte.
    """
    path = resolve_project_path(path)
    return path.stat().st_size


# ============================================================
# JSON HELPERS
# ============================================================

def read_json(path: str | Path, default=None):
    """
    Đọc file JSON.
    """
    path = resolve_project_path(path)

    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(data, path: str | Path, indent: int = 4) -> Path:
    """
    Ghi file JSON UTF-8.
    """
    path = ensure_parent_dir(path)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)

    logger.info("Wrote JSON file: %s", path)
    return path


# ============================================================
# PIPELINE FOLDER SETUP
# ============================================================

def setup_pipeline_folders() -> dict[str, Path]:
    """
    Tạo sẵn các folder chính cho pipeline.
    Gọi ở đầu run_pipeline.py.

    Returns:
        Dictionary mapping folder names to Path objects
    """
    from src.config.settings import (
        BACKUP_DIR,
        INCOMING_EML_DIR,
        PROCESSED_SUCCESS_EML_DIR,
        PROCESSED_FAILED_EML_DIR,
        STAGING_DIR,
        CLEANED_DIR,
        MAPPING_DIR,
        QUALITY_CHECK_DIR,
        LOG_DIR,
    )

    folders = {
        "backup": BACKUP_DIR,
        "incoming_eml": INCOMING_EML_DIR,
        "incoming_pdf": ensure_dir("data/incoming/pdf"),
        "processed_success_eml": PROCESSED_SUCCESS_EML_DIR.parent,
        "processed_failed_eml": PROCESSED_FAILED_EML_DIR.parent,
        "staging": STAGING_DIR,
        "cleaned": CLEANED_DIR,
        "mapping": MAPPING_DIR,
        "quality_check": QUALITY_CHECK_DIR,
        "logs": LOG_DIR,
    }

    created = {}

    for key, folder in folders.items():
        created[key] = ensure_dir(folder)

    logger.info("Pipeline folders are ready")
    return created


# ============================================================
# ORDER FILE HELPERS
# ============================================================

def normalize_so_number(value: str) -> Optional[str]:
    """
    Chuẩn hóa số đơn hàng về dạng BH26.0935.
    Nhận các dạng:
        BH26.0935
        BH26_0935
        BH26-0935
    """
    if not value:
        return None

    text = str(value).upper().strip()

    match = re.search(r"(BH\d{2})[._-](\d{4})", text)

    if not match:
        return None

    return f"{match.group(1)}.{match.group(2)}"


def extract_so_number_from_filename(path: str | Path) -> Optional[str]:
    """
    Trích số đơn từ tên file.
    Ví dụ:
        BH26_0935.pdf -> BH26.0935
    """
    path = to_path(path)
    return normalize_so_number(path.stem)


# ============================================================
# MANUAL TEST
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    folders = setup_pipeline_folders()

    print("Project root:", get_project_root())
    print("Incoming EML:", folders["incoming_eml"])

    eml_files = list_eml_files("data/incoming/eml")
    print(f"Found {len(eml_files)} .eml files")