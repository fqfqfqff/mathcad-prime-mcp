"""Mathcad Prime text blocks.

Prime keeps the content of a text region in a nested WPF XamlPackage part,
referenced from worksheet.xml.rels; the <text> element in worksheet.xml only
carries the formatting. Building one therefore means emitting three things at
once: the region, the package part, and the relationship that joins them.
"""

from __future__ import annotations

import io
import uuid
import zipfile

FONT = 'FontFamily="Tahoma" FontStyle="Normal" FontWeight="Normal" FontSize="14.666666666666668"'

_SECTION = (
    '<Section xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
    'xml:space="preserve" TextAlignment="Left" LineHeight="Auto" '
    'IsHyphenationEnabled="False" xml:lang="ru-ru" FlowDirection="LeftToRight" '
    'NumberSubstitution.CultureSource="User" NumberSubstitution.Substitution="AsCulture" '
    + FONT + ' FontStretch="Normal" Foreground="#FF000000" '
    'Typography.Variants="Normal">%s</Section>')

_INNER_RELS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Type="http://schemas.microsoft.com/wpf/2005/10/xaml/entry" '
    'Target="/Xaml/Document.xaml" Id="R%s" /></Relationships>')

_INNER_CT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xaml" ContentType="application/vnd.ms-wpf.xaml+xml" />'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml" /></Types>')

FLOW_DOC = (
    '<FlowDocument ' + FONT + ' Foreground="#FF000000" Background="#00FFFFFF" '
    'TextAlignment="Left" xml:lang="ru-ru" Typography.Variants="Normal" '
    'xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" />')

LINE_H = 17.71
CHARS_PER_LINE = 92


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def new_id() -> str:
    return "R" + uuid.uuid4().hex[:16]


def xaml_package(text: str) -> bytes:
    """The nested zip that holds one text block's content."""
    paragraphs = "".join(
        '<Paragraph><Run style="Normal">%s</Run></Paragraph>' % _esc(line)
        for line in (text.split("\n") or [""]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Xaml/Document.xaml", (_SECTION % paragraphs).encode("utf-8"))
        z.writestr("_rels/.rels", (_INNER_RELS % uuid.uuid4().hex[:16]).encode("utf-8"))
        z.writestr("[Content_Types].xml", _INNER_CT.encode("utf-8"))
    return buf.getvalue()


def height_for(text: str, width_chars: int = CHARS_PER_LINE) -> float:
    lines = 0
    for para in text.split("\n"):
        lines += max(1, -(-len(para) // width_chars))
    return round(LINE_H * lines, 2)


def region(rid: int, text: str, top: float, left: float, width: float,
           part_index: int) -> tuple[str, str, str, bytes]:
    """Returns (region xml, relationship id, part name, part bytes)."""
    rel_id = new_id()
    part = "mathcad/xaml/FlowDocument%d.XamlPackage" % part_index
    xml = ('<region region-id="%d" width="%s" actualWidth="%s" actualHeight="%s" '
           'top="%s" left="%s"><text is-text-block="true" item-idref="%s">%s</text></region>'
           % (rid, width, width, height_for(text), top, left, rel_id, FLOW_DOC))
    return xml, rel_id, part, xaml_package(text)
