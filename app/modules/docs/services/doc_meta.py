import io
import json
import logging
import zipfile
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

META_NS = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
META_KEY = "x-locoroo-meta"


def inject_metadata(file_bytes, metadata):
    blob = json.dumps(metadata, ensure_ascii=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zin:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "meta.xml":
                    data = _patch_meta_xml(data, blob)
                zout.writestr(item, data)
    buf.seek(0)
    return buf.read()


def extract_metadata(file_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            if "meta.xml" not in zf.namelist():
                return None
            meta_xml = zf.read("meta.xml")
    except (zipfile.BadZipFile, KeyError):
        return None
    return _parse_meta_xml(meta_xml)


def write_sidecar_metadata(metadata):
    return json.dumps(metadata, ensure_ascii=False, indent=2)


def read_sidecar_metadata(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _patch_meta_xml(xml_bytes, blob):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        root = _build_empty_meta_tree()

    meta_elem = root.find(f"{{{META_NS}}}meta")
    if meta_elem is None:
        meta_elem = ET.SubElement(root, f"{{{META_NS}}}meta")

    for child in list(meta_elem):
        tag = child.tag
        if tag == f"{{{META_NS}}}user-defined" and child.get(f"{{{META_NS}}}name") == META_KEY:
            meta_elem.remove(child)

    ud = ET.SubElement(meta_elem, f"{{{META_NS}}}user-defined")
    ud.set(f"{{{META_NS}}}name", META_KEY)
    ud.text = blob

    return ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8")


def _parse_meta_xml(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    meta_elem = root.find(f"{{{META_NS}}}meta")
    if meta_elem is None:
        return None

    for child in meta_elem:
        if child.tag == f"{{{META_NS}}}user-defined" and child.get(f"{{{META_NS}}}name") == META_KEY:
            try:
                text = child.text
                if not text:
                    return None
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse %s from meta.xml", META_KEY)
                return None
    return None


def _build_empty_meta_tree():
    office_ns = OFFICE_NS
    root = ET.Element(f"{{{office_ns}}}document-meta")
    root.set(f"{{{office_ns}}}version", "1.2")
    ET.SubElement(root, f"{{{META_NS}}}meta")
    return root
