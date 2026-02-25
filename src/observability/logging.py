"""Structured logging with stdlib fallback."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any


def _build_stdlib_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_name = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> Any:
    """Return structlog logger when installed; else stdlib logger."""
    try:
        import structlog

        level_name = os.getenv("APP_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.stdlib.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        )
        return structlog.get_logger(name)
    except ImportError:
        return _build_stdlib_logger(name)
