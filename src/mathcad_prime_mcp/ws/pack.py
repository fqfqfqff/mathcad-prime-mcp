"""Reading and writing the .mcdx container (a zip with a fixed part layout)."""

from __future__ import annotations

import io
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template.mcdx")

WORKSHEET = "mathcad/worksheet.xml"
RESULTS = "mathcad/result.xml"


CONTENT_TYPES = "[Content_Types].xml"
WS_RELS = "mathcad/_rels/worksheet.xml.rels"

_RELS_TPL = ('<?xml version="1.0" encoding="utf-8"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '%s</Relationships>')
_REL = ('<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/flowDocument" Target="/%s" Id="%s" />')
_XAML_CT = '<Default Extension="XamlPackage" ContentType="application/zip" />'


def write_mcdx(worksheet_xml: str, dst: str, template: str = TEMPLATE,
               parts: dict | None = None, rels: list | None = None) -> str:
    """Build a .mcdx from a worksheet.xml body, taking every other part from the template.

    `parts` adds extra package parts (text block XamlPackages) and `rels` the
    (relationship id, part name) pairs that worksheet.xml refers to.
    """
    src = zipfile.ZipFile(template)
    names = src.namelist()
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name in names:
            if name == WORKSHEET:
                out.writestr(name, worksheet_xml.encode("utf-8"))
            elif name == CONTENT_TYPES and parts:
                ct = src.read(name).decode("utf-8-sig")
                if "XamlPackage" not in ct:
                    ct = ct.replace("<Default Extension=\"rels\"", _XAML_CT + "<Default Extension=\"rels\"", 1)
                out.writestr(name, ct.encode("utf-8"))
            else:
                out.writestr(name, src.read(name))
        for name, data in (parts or {}).items():
            out.writestr(name, data)
        if rels:
            body = "".join(_REL % (target, rid) for rid, target in rels)
            out.writestr(WS_RELS, (_RELS_TPL % body).encode("utf-8"))
    src.close()
    with open(dst, "wb") as fh:
        fh.write(buf.getvalue())
    return dst


def read_part(path: str, part: str) -> str | None:
    with zipfile.ZipFile(path) as z:
        if part not in z.namelist():
            return None
        return z.read(part).decode("utf-8-sig")
