import io
import logging
import xml.etree.ElementTree as ET

import requests

from flask import current_app

logger = logging.getLogger(__name__)

_discovery_cache = {}


def get_edit_url(doc_type, collabora_url=None):
    url = collabora_url or _default_url()
    if not url:
        return None
    ext_map = {"odt": "odt", "ods": "ods", "odp": "odp", "odg": "odg"}
    ext = ext_map.get(doc_type, "odt")
    action = "edit"
    try:
        discovery_url = f"{url}/hosting/discovery"
        cached = _discovery_cache.get(url)
        if cached and cached.get("url"):
            return cached["url"]
        resp = requests.get(discovery_url, timeout=5)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        for app in root.findall(".//app"):
            for act in app.findall("action"):
                if act.get("ext") == ext and act.get("name") == action:
                    edit_url = act.get("urlsrc", "")
                    _discovery_cache[url] = {"url": edit_url}
                    return edit_url
    except Exception:
        logger.exception("Failed to fetch Collabora discovery for edit URL")
    return None


def convert_to_odt(file_stream, source_filename, collabora_url=None):
    return _convert(file_stream, source_filename, "odt", collabora_url)


def convert_to_ods(file_stream, source_filename, collabora_url=None):
    return _convert(file_stream, source_filename, "ods", collabora_url)


def convert_to_odp(file_stream, source_filename, collabora_url=None):
    return _convert(file_stream, source_filename, "odp", collabora_url)


def convert_upload(file_stream, source_filename, target_type="odt", collabora_url=None):
    return _convert(file_stream, source_filename, target_type, collabora_url)


class ConversionError(RuntimeError):
    pass


def _convert(file_stream, source_filename, target_type, collabora_url=None):
    url = collabora_url or _default_url()
    if not url:
        raise ConversionError("Collabora URL not configured")
    ext = _extension(source_filename)
    convert_url = f"{url}/cool/convert-to"
    raw = file_stream.read() if hasattr(file_stream, "read") else file_stream
    files = {
        "data": (source_filename, raw, _mime_for_ext(ext)),
    }
    data = {"format": target_type}
    try:
        resp = requests.post(convert_url, files=files, data=data, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Collabora conversion failed for %s -> %s", source_filename, target_type)
        raise ConversionError(f"Collabora conversion failed: {exc}") from exc
    except Exception as exc:
        raise ConversionError(f"Collabora conversion failed: {exc}") from exc

    content = resp.content
    if not _is_valid_output(content, target_type):
        logger.error(
            "Collabora returned invalid output for %s -> %s (size=%d, head=%r)",
            source_filename, target_type, len(content), content[:20],
        )
        raise ConversionError(
            f"Collabora returned invalid output for {source_filename} -> {target_type}"
        )

    return io.BytesIO(content)


def is_collabora_available(collabora_url=None):
    url = collabora_url or _default_url()
    if not url:
        return False
    try:
        resp = requests.get(f"{url}/hosting/discovery", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _is_valid_output(content: bytes, target_type: str) -> bool:
    if not content or len(content) < 4:
        return False
    if target_type == "pdf":
        return content[:4] == b"%PDF"
    return content[:4] == b"PK\x03\x04"


def _default_url():
    return (
        current_app.config.get("COLLABORA_INTERNAL_URL")
        or current_app.config.get("COLLABORA_URL", "http://localhost:9980")
    )


def _extension(filename):
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _mime_for_ext(ext):
    mimes = {
        "odt": "application/vnd.oasis.opendocument.text",
        "ods": "application/vnd.oasis.opendocument.spreadsheet",
        "odp": "application/vnd.oasis.opendocument.presentation",
        "odg": "application/vnd.oasis.opendocument.graphics",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "doc": "application/msword",
        "xls": "application/vnd.ms-excel",
        "ppt": "application/vnd.ms-powerpoint",
        "pdf": "application/pdf",
        "html": "text/html",
        "htm": "text/html",
        "rtf": "application/rtf",
        "epub": "application/epub+zip",
        "txt": "text/plain",
        "md": "text/markdown",
        "markdown": "text/markdown",
        "csv": "text/csv",
        "tsv": "text/tab-separated-values",
    }
    return mimes.get(ext, "application/octet-stream")
