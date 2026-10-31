"""
Scheduled Automation Runner Module
Executes inventory automation at configurable intervals with single-instance
locking to prevent overlapping executions and graceful shutdown handling.
"""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import schedule

from config.settings import (
    SCHEDULE_INTERVAL_MINUTES,
    SCHEDULE_LOCK_FILE,
    setup_logging,
)
from main import run_pipeline

logger = setup_logging(log_filename="scheduler.log")


class SingleInstanceLock:
    """
    Manages process-level lock file to prevent overlapping runs.
    Handles stale locks and ensures clean release on exit.
    """

    def __init__(self, lock_file: Path = SCHEDULE_LOCK_FILE):
        self.lock_file = Path(lock_file)
        self.is_locked = False

    def is_process_running(self, pid: int) -> bool:
        """Checks if a process with given PID is alive."""
        if pid <= 0:
            return False
        try:
            # On Windows/Unix, sending signal 0 checks for process existence without killing it
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
        except PermissionError:
            # Process exists but is owned by another user/admin
            return True

    def acquire(self) -> bool:
        """
        Attempts to acquire the lock. Returns True if acquired, False otherwise.
        """
        if self.lock_file.exists():
            try:
                content = self.lock_file.read_text(encoding="utf-8").strip()
                parts = content.split(":")
                pid = int(parts[0]) if parts else 0

                if self.is_process_running(pid):
                    logger.warning(
                        "Previous run is still executing (PID: %d). Skipping this scheduled tick.",
                        pid,
                    )
                    return False
                else:
                    logger.warning(
                        "Found stale lock file for non-running PID %d. Reclaiming lock.",
                        pid,
                    )
                    self.lock_file.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Error reading lock file (%s). Overwriting lock.", exc)
                self.lock_file.unlink(missing_ok=True)

        try:
            timestamp = datetime.now().isoformat()
            self.lock_file.write_text(f"{os.getpid()}:{timestamp}", encoding="utf-8")
            self.is_locked = True
            return True
        except Exception as exc:
            logger.error("Failed to create lock file '%s': %s", self.lock_file, exc)
            return False

    def release(self) -> None:
        """Releases the lock file."""
        if self.is_locked:
            try:
                if self.lock_file.exists():
                    self.lock_file.unlink()
                self.is_locked = False
                logger.debug("Released lock file '%s'.", self.lock_file)
            except Exception as exc:
                logger.error("Failed to release lock file '%s': %s", self.lock_file, exc)


def scheduled_job(demo_mode: bool = False, lock: Optional[SingleInstanceLock] = None) -> None:
    """Wrapper that acquires the lock, executes the pipeline, and safely releases the lock."""
    if lock and not lock.acquire():
        return

    try:
        logger.info("Executing scheduled inventory pipeline...")
        run_pipeline(demo_mode=demo_mode)
    except Exception as exc:
        logger.critical("Unhandled exception in scheduled execution: %s", exc, exc_info=True)
    finally:
        if lock:
            lock.release()


def parse_arguments() -> argparse.Namespace:
    """Parses scheduler CLI flags."""
    parser = argparse.ArgumentParser(
        description="Vendor Inventory Automation Scheduler",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=SCHEDULE_INTERVAL_MINUTES,
        help="Interval between execution runs in minutes",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run scheduled jobs in demo mode (simulated inbox)",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        default=True,
        help="Run an initial cycle immediately before entering scheduled loop",
    )
    return parser.parse_args()


def main() -> None:
    """Initializes and runs the schedule daemon."""
    args = parse_arguments()
    lock = SingleInstanceLock()

    # Graceful shutdown handler
    def handle_shutdown(signum, frame):
        logger.info("Received termination signal (%d). Shutting down scheduler gracefully...", signum)
        lock.release()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    logger.info("=" * 60)
    logger.info("Vendor Inventory Scheduler Started")
    logger.info("Schedule Interval : Every %d minute(s)", args.interval)
    logger.info("Demo Mode         : %s", args.demo)
    logger.info("Process PID       : %d", os.getpid())
    logger.info("=" * 60)

    # Initial execution
    if args.run_now:
        logger.info("Running initial pipeline execution on startup...")
        scheduled_job(demo_mode=args.demo, lock=lock)

    # Register recurring job
    schedule.every(args.interval).minutes.do(scheduled_job, demo_mode=args.demo, lock=lock)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        handle_shutdown(0, None)


if __name__ == "__main__":
    main()
