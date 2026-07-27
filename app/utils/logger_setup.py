"""Shared loguru configuration for all engines.
Call `setup_logging(name)` once per process at startup.

Every script can then use the standard loguru logger:
    from loguru import logger
    logger.info("hello")
    logger.error("something broke")
"""
import sys
from pathlib import Path
from loguru import logger

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_configured = set()


def setup_logging(name: str = "realtor", level: str = "DEBUG"):
    if name in _configured:
        return
    _configured.add(name)

    logger.remove()

    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{name}</cyan> | <level>{message}</level>",
        level="INFO",
        colorize=True,
    )

    logger.add(
        LOG_DIR / f"{name}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{function}:{line} | {message}",
        rotation="10 MB",
        retention="30 days",
        level=level,
    )


def setup_request_logging(app):
    """Log every HTTP request/response via Flask's after_request hook."""
    from flask import g, request
    import time

    @app.before_request
    def _start_timer():
        g._start = time.time()

    @app.after_request
    def _log_request(response):
        elapsed = time.time() - g._start
        logger.info(
            "{method} {path} -> {status} ({elapsed:.0f}ms)",
            method=request.method,
            path=request.path,
            status=response.status_code,
            elapsed=elapsed * 1000,
        )
        return response

    return app
