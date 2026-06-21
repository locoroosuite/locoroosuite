import io
import zipfile


def empty_odt():
    return _build_odf("text")


def empty_ods():
    return _build_odf("spreadsheet")


def empty_odp():
    return _build_odf("presentation")


def empty_odg():
    return _build_odf("drawing")


DOC_TYPES = {
    "odt": "text",
    "ods": "spreadsheet",
    "odp": "presentation",
    "odg": "drawing",
}

TYPE_NAMES = {
    "odt": "Untitled Document",
    "ods": "Untitled Spreadsheet",
    "odp": "Untitled Presentation",
    "odg": "Untitled Drawing",
}

MIME_TYPES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "odg": "application/vnd.oasis.opendocument.graphics",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

EXTENSIONS = {
    "text": "odt",
    "spreadsheet": "ods",
    "presentation": "odp",
    "drawing": "odg",
}


def _build_odf(kind):
    buf = io.BytesIO()
    manifest_ns = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
    office_ns = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    table_ns = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    draw_ns = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"

    mimetype = MIME_TYPES[EXTENSIONS[kind]]

    _common_attrs = f'xmlns:office="{office_ns}" office:version="1.2"'
    _common_children = "<office:scripts/><office:font-face-decls/><office:automatic-styles/>"

    if kind == "text":
        body = (
            f"<office:body>"
            f"<office:text>"
            f'<text:p xmlns:text="{text_ns}"/>'
            f"</office:text>"
            f"</office:body>"
        )
    elif kind == "spreadsheet":
        body = (
            f"<office:body>"
            f"<office:spreadsheet>"
            f'<table:table xmlns:table="{table_ns}" table:name="Sheet1"/>'
            f"</office:spreadsheet>"
            f"</office:body>"
        )
    elif kind == "presentation":
        body = (
            f"<office:body>"
            f"<office:presentation>"
            f'<draw:page xmlns:draw="{draw_ns}" draw:name="page1"/>'
            f"</office:presentation>"
            f"</office:body>"
        )
    elif kind == "drawing":
        body = (
            f"<office:body>"
            f"<office:drawing>"
            f'<draw:page xmlns:draw="{draw_ns}" draw:name="page1"/>'
            f"</office:drawing>"
            f"</office:body>"
        )
    else:
        body = "<office:body/>"

    content = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content {_common_attrs}>'
        f"{_common_children}"
        f"{body}"
        f"</office:document-content>"
    )

    manifest = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<manifest:manifest xmlns:manifest="{manifest_ns}" manifest:version="1.2">'
        f'<manifest:file-entry manifest:media-type="{mimetype}" manifest:version="1.2" manifest:full-path="/"/>'
        f'<manifest:file-entry manifest:media-type="text/xml" manifest:full-path="content.xml"/>'
        f'<manifest:file-entry manifest:media-type="text/xml" manifest:full-path="styles.xml"/>'
        f'<manifest:file-entry manifest:media-type="text/xml" manifest:full-path="meta.xml"/>'
        f"</manifest:manifest>"
    )

    styles = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-styles {_common_attrs}>'
        f"<office:font-face-decls/>"
        f"<office:styles/>"
        f"<office:automatic-styles/>"
        f"<office:master-styles/>"
        f"</office:document-styles>"
    )

    meta_xml = _build_meta_xml()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        zf.writestr("content.xml", content)
        zf.writestr("META-INF/manifest.xml", manifest)
        zf.writestr("styles.xml", styles)
        zf.writestr("meta.xml", meta_xml)

    buf.seek(0)
    return buf


def _build_meta_xml():
    meta_ns = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
    office_ns = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-meta xmlns:office="{office_ns}" xmlns:meta="{meta_ns}" office:version="1.2">'
        f"<meta:meta/>"
        f"</office:document-meta>"
    )
