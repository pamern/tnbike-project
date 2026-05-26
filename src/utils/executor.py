# ============================================================
# src/utils/executor.py
# Unified step executor for pipeline and preprocessing
# ============================================================

import time
import subprocess
import logging
from typing import Any, Callable, Optional

from src.utils.time_utils import format_seconds


logger = logging.getLogger(__name__)


class PipelineStepExecutor:
    """
    Unified executor for running pipeline steps.

    Supports both:
    - Direct function calls
    - Subprocess command execution

    Handles timing, logging, and error management.
    """

    def __init__(self, dry_run: bool = False, continue_on_error: bool = False):
        """
        Args:
            dry_run: If True, log commands without executing
            continue_on_error: If True, don't raise on step failure
        """
        self.dry_run = dry_run
        self.continue_on_error = continue_on_error

    def run_function(
        self,
        step_name: str,
        func: Callable,
        *args,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Run a function as a step.

        Args:
            step_name: Display name for the step
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function

        Returns:
            Result dictionary with status, timing, result data
        """
        logger.info("")
        logger.info("=" * 80)
        logger.info("START STEP | %s", step_name)
        logger.info("=" * 80)

        start = time.perf_counter()

        try:
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start

            logger.info("=" * 80)
            logger.info("END STEP SUCCESS | %s | elapsed=%s", step_name, format_seconds(elapsed))
            logger.info("=" * 80)

            return {
                "step": step_name,
                "status": "SUCCESS",
                "elapsed": format_seconds(elapsed),
                "result": result,
            }

        except Exception as e:
            elapsed = time.perf_counter() - start

            logger.exception(
                "END STEP FAILED | %s | elapsed=%s | error=%s",
                step_name,
                format_seconds(elapsed),
                e,
            )

            if not self.continue_on_error:
                raise

            return {
                "step": step_name,
                "status": "FAILED",
                "elapsed": format_seconds(elapsed),
                "error": str(e),
                "result": None,
            }

    def run_subprocess(
        self,
        step_name: str,
        command: list[str],
    ) -> dict[str, Any]:
        """
        Run a command as a subprocess step.

        Args:
            step_name: Display name for the step
            command: Command as list (e.g., ["python", "-m", "module"])

        Returns:
            Result dictionary with status, timing, returncode
        """
        logger.info("")
        logger.info("=" * 80)
        logger.info("START STEP | %s", step_name)
        logger.info("=" * 80)
        logger.info("Command: %s", " ".join(command))

        start = time.perf_counter()

        if self.dry_run:
            elapsed = time.perf_counter() - start
            logger.warning("DRY RUN: command was not executed")
            logger.info("END STEP DRY RUN | %s | elapsed=%s", step_name, format_seconds(elapsed))

            return {
                "step": step_name,
                "command": " ".join(command),
                "status": "DRY_RUN",
                "returncode": None,
                "elapsed": format_seconds(elapsed),
            }

        try:
            completed = subprocess.run(
                command,
                check=True,
                text=True,
            )

            elapsed = time.perf_counter() - start

            logger.info("=" * 80)
            logger.info("END STEP SUCCESS | %s | elapsed=%s", step_name, format_seconds(elapsed))
            logger.info("=" * 80)

            return {
                "step": step_name,
                "command": " ".join(command),
                "status": "SUCCESS",
                "returncode": completed.returncode,
                "elapsed": format_seconds(elapsed),
            }

        except subprocess.CalledProcessError as e:
            elapsed = time.perf_counter() - start

            logger.exception(
                "END STEP FAILED | %s | elapsed=%s | returncode=%s",
                step_name,
                format_seconds(elapsed),
                e.returncode,
            )

            if not self.continue_on_error:
                raise

            return {
                "step": step_name,
                "command": " ".join(command),
                "status": "FAILED",
                "returncode": e.returncode,
                "elapsed": format_seconds(elapsed),
                "error": str(e),
            }

    def run_step(
        self,
        step_name: str,
        func: Optional[Callable] = None,
        command: Optional[list[str]] = None,
        *args,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Run a step (either function or subprocess).

        Exactly one of func or command must be provided.

        Args:
            step_name: Display name for the step
            func: Function to execute (mutually exclusive with command)
            command: Command list to execute (mutually exclusive with func)
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function

        Returns:
            Result dictionary
        """
        if func is not None and command is not None:
            raise ValueError("Provide either func or command, not both")
        if func is None and command is None:
            raise ValueError("Provide either func or command")

        if func is not None:
            return self.run_function(step_name, func, *args, **kwargs)
        else:
            return self.run_subprocess(step_name, command)
