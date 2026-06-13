from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

PANDOC_EXTENSIONS: dict[str, dict[str, Any]] = {
    "docx":  {"pandoc_reader": "docx",  "target_type": "odt", "view": True},
    "odt":   {"pandoc_reader": "odt",   "target_type": "odt", "view": True},
    "rtf":   {"pandoc_reader": "rtf",   "target_type": "odt", "view": True},
    "epub":  {"pandoc_reader": "epub",  "target_type": "odt", "view": True},
    "html":  {"pandoc_reader": "html",  "target_type": "odt", "view": True},
    "htm":   {"pandoc_reader": "html",  "target_type": "odt", "view": True},
    "tex":   {"pandoc_reader": "latex", "target_type": "odt", "view": True},
    "latex": {"pandoc_reader": "latex", "target_type": "odt", "view": True},
    "md":    {"pandoc_reader": "markdown", "target_type": "odt", "view": True},
    "markdown": {"pandoc_reader": "markdown", "target_type": "odt", "view": True},
    "txt":   {"pandoc_reader": "plain", "target_type": "odt", "view": True},
    "org":   {"pandoc_reader": "org",   "target_type": "odt", "view": True},
    "rst":   {"pandoc_reader": "rst",   "target_type": "odt", "view": True},
    "docbook": {"pandoc_reader": "docbook", "target_type": "odt", "view": True},
    "opml":  {"pandoc_reader": "opml",  "target_type": "odt", "view": True},
    "csv":   {"pandoc_reader": "csv",   "target_type": "odt", "view": True},
    "tsv":   {"pandoc_reader": "tsv",   "target_type": "odt", "view": True},
    "ipynb": {"pandoc_reader": "ipynb", "target_type": "odt", "view": True},
    "xlsx":  {"pandoc_reader": None,    "target_type": "ods", "view": False},
    "xls":   {"pandoc_reader": None,    "target_type": "ods", "view": False},
    "pptx":  {"pandoc_reader": None,    "target_type": "odp", "view": False},
    "ppt":   {"pandoc_reader": None,    "target_type": "odp", "view": False},
    "ods":   {"pandoc_reader": None,    "target_type": "ods", "view": False},
    "odp":   {"pandoc_reader": None,    "target_type": "odp", "view": False},
    "pdf":   {"pandoc_reader": None,    "target_type": "odt", "view": True, "native_view": True},
    "jpg":   {"pandoc_reader": None,    "target_type": None,  "view": True, "native_view": True},
    "jpeg":  {"pandoc_reader": None,    "target_type": None,  "view": True, "native_view": True},
    "png":   {"pandoc_reader": None,    "target_type": None,  "view": True, "native_view": True},
    "gif":   {"pandoc_reader": None,    "target_type": None,  "view": True, "native_view": True},
    "svg":   {"pandoc_reader": None,    "target_type": None,  "view": True, "native_view": True},
    "webp":  {"pandoc_reader": None,    "target_type": None,  "view": True, "native_view": True},
    "bmp":   {"pandoc_reader": None,    "target_type": None,  "view": True, "native_view": True},
}

PANDOC_UPLOAD_EXTENSIONS: set[str] = set(PANDOC_EXTENSIONS.keys())


def get_attachment_actions(filename: str) -> dict[str, Any]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    info = PANDOC_EXTENSIONS.get(ext)
    if not info:
        return {"download": True, "view": False, "open_in_docs": False}
    return {
        "download": True,
        "view": info["view"],
        "open_in_docs": info["target_type"] is not None,
        "target_type": info["target_type"],
        "pandoc_reader": info["pandoc_reader"],
        "native_view": info.get("native_view", False),
    }


def convert_to_html(data: bytes, pandoc_reader: str, timeout: int = 30) -> str | None:
    try:
        result = subprocess.run(
            ["pandoc", "-f", pandoc_reader, "-t", "html", "--standalone"],
            input=data,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error(
                "pandoc to-html failed reader=%s rc=%d stderr=%s",
                pandoc_reader, result.returncode, result.stderr.decode(errors="replace")[:500],
            )
            return None
        return result.stdout.decode("utf-8", errors="replace")
    except FileNotFoundError:
        logger.error("pandoc binary not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.error("pandoc to-html timed out reader=%s", pandoc_reader)
        return None
    except Exception:
        logger.exception("pandoc to-html unexpected error reader=%s", pandoc_reader)
        return None


def convert_to_odf(data: bytes, pandoc_reader: str, target_type: str = "odt", timeout: int = 30) -> bytes | None:
    try:
        result = subprocess.run(
            ["pandoc", "-f", pandoc_reader, "-t", target_type],
            input=data,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error(
                "pandoc to-%s failed reader=%s rc=%d stderr=%s",
                target_type, pandoc_reader, result.returncode, result.stderr.decode(errors="replace")[:500],
            )
            return None
        return result.stdout
    except FileNotFoundError:
        logger.error("pandoc binary not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.error("pandoc to-%s timed out reader=%s", target_type, pandoc_reader)
        return None
    except Exception:
        logger.exception("pandoc to-%s unexpected error reader=%s", target_type, pandoc_reader)
        return None
