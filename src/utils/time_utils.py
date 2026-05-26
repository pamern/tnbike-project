# ============================================================
# src/utils/time_utils.py
# Utility functions for time formatting and conversion
# ============================================================

from datetime import datetime


def now_text(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Get current datetime as formatted string.

    Args:
        fmt: datetime format string (default: "%Y-%m-%d %H:%M:%S")

    Returns:
        Formatted datetime string
    """
    return datetime.now().strftime(fmt)


def format_seconds(seconds: float) -> str:
    """
    Format seconds into human-readable string (e.g., "1m 30.50s").

    Args:
        seconds: elapsed time in seconds

    Returns:
        Formatted time string (e.g., "0.50s", "2m 30.50s")
    """
    if seconds < 60:
        return f"{seconds:.2f}s"

    minutes = int(seconds // 60)
    remain_seconds = seconds % 60

    return f"{minutes}m {remain_seconds:.2f}s"
