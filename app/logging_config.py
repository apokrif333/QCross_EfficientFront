"""Console output and bounded log files for ingestion commands."""

import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings


def configure_logging(settings: Settings, command: str, *, verbose: bool = False) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    path = (settings.log_dir / f"{command}.log").resolve()
    for handler in root.handlers[:]:
        if handler.name == "qcross-file" and handler.baseFilename != str(path):
            root.removeHandler(handler)
            handler.close()
    if not any(
        isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(path)
        for handler in root.handlers
    ):
        handler = RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.set_name("qcross-file")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    if not any(type(handler) is logging.StreamHandler for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    root.setLevel("DEBUG" if verbose else settings.log_level.upper())
