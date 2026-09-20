"""Compact worksheet source -> Mathcad Prime worksheet.xml.

Source format (one row of regions per line, ';' separates regions on a row)::

    # комментарий
    S := READWAV("lab4.wav") ; m := floor(max(S)*0.1) ; L := length(S)
    n := 0..L-1 ; D[n] := rnd(m)-m/2 ; V[n] := S[n]+D[n]
    WRITEWAV("lab4_sh.wav",44100,16,V)= ; N := 2^ceil(log(L,2))
    L= ; m= ; N= ; sk=
    @plot x=n y=S[n]:red:2,V[n]:navy:1 xlim=0..200 size=620x260
    @gap 40

`expr=` is an evaluation region (shows the value), `lhs := rhs` a definition.
Rows are positioned automatically and never straddle a page break.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import textblock
from .expr import define_xml, eval_xml, parse_expr, to_xml

PAGE_H = 990.0          # printable height of one A4 page, in worksheet units
LEFT0 = 18.9
TOP0 = 18.9
COL_GAP = 30.0
ROW_GAP = 34.0
PAGE_MARGIN = 12.0      # keep this far away from a page boundary
BASE_H = 25.6           # height of a plain one-line formula
# Сколько единиц документа видно в окне Prime при Ctrl+Home: график для
# картинки должен целиком помещаться в этот просвет, иначе его срежет краем
VISIBLE_H = 720.0

COLORS = {
    "navy": "#FF00008B", "darkblue": "#FF00008B", "blue": "#FF0000FF",
    "red": "#FFFF0000", "green": "#FF008000", "black": "#FF000000",
    "magenta": "#FFFF00FF", "orange": "#FFFF8C00", "gray": "#FF808080",
}
CYCLE = ["navy", "red", "green", "black"]

HEAD = ('<worksheet msg-id="NoMessage" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:ve="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:ws="http://schemas.mathsoft.com/worksheet50" '
        'xmlns:ml="http://schemas.mathsoft.com/math50" '
        'xmlns:u="http://schemas.mathsoft.com/units10" '
        'xmlns:p="http://schemas.mathsoft.com/provenance10" '
        'xmlns="http://schemas.mathsoft.com/worksheet50"><regions>')
TAIL = "</regions></worksheet>"

RESFMT = ('<resultFormat><matrix size="12,12" offset="0,0" show-indices="false" '
          'expand-nested-arrays="false" /></resultFormat>')


# --------------------------------------------------------------------------
# splitting helpers that respect quotes / brackets
# --------------------------------------------------------------------------
def strip_comment(line: str) -> str:
    """Drop a trailing '#' comment, ignoring '#' inside a string literal."""
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:i]
    return line


def split_top(text: str, sep: str) -> list[str]:
    out, depth, buf, in_str = [], 0, [], False
    for ch in text:
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == sep and depth == 0:
                out.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    out.append("".join(buf))
    return [p.strip() for p in out if p.strip()]


# --------------------------------------------------------------------------
# region model
# --------------------------------------------------------------------------
@dataclass
class Region:
    xml: str
    width: float
    height: float
    src: str


def _estimate(src: str) -> tuple[float, float]:
    w = max(60.0, 9.2 * len(src) + 18.0)
    if "/" in src:
        h = 45.0
    elif "^" in src or "[" in src:
        h = 36.8
    else:
        h = 25.6
    return w, h


def math_region(src: str) -> Region:
    body = src.strip()
    if body.endswith("="):
        node = parse_expr(body[:-1])
        xml = eval_xml(node)
    elif ":=" in body:
        lhs_s, rhs_s = body.split(":=", 1)
        xml = define_xml(parse_expr(lhs_s), parse_expr(rhs_s))
    else:
        raise SyntaxError(f"регион должен быть 'x := выражение' или 'выражение=': {body!r}")
    w, h = _estimate(body)
    return Region(xml, w, h, body)


# --------------------------------------------------------------------------
# plots
# --------------------------------------------------------------------------
@dataclass
class Plot:
    x: list[str]
    traces: list[tuple[str, str, int]]   # expr, color hex, weight
    xlim: tuple | None = None
    ylim: tuple | None = None
    size: tuple = (620.0, 260.0)
    src: str = ""


def parse_plot(line: str) -> Plot:
    """@plot x=<expr> y=<e>:<color>:<w>,... [xlim=a..b] [ylim=a..b] [size=WxH]"""
    rest = line[len("@plot"):].strip()
    parts = dict()
    for chunk in re.findall(r"(\w+)=(\S+)", rest):
        parts[chunk[0]] = chunk[1]
    if "x" not in parts or "y" not in parts:
        raise SyntaxError("в @plot нужны x= и y=")

    traces = []
    for i, item in enumerate(split_top(parts["y"], ",")):
        bits = item.split(":")
        expr = bits[0]
        color = COLORS.get(bits[1] if len(bits) > 1 else CYCLE[i % len(CYCLE)],
                           COLORS[CYCLE[i % len(CYCLE)]])
        weight = int(bits[2]) if len(bits) > 2 else 1
        traces.append((expr, color, weight))

    def lim(key):
        if key not in parts:
            return None
        a, b = parts[key].split("..")
        return (a, b)

    size = (620.0, 260.0)
    if "size" in parts:
        w, h = parts["size"].lower().split("x")
        size = (float(w), float(h))

    xs = split_top(parts["x"], ",")
    if len(xs) == 1:
        xs = xs * len(traces)
    return Plot(xs, traces, lim("xlim"), lim("ylim"), size, line.strip())


PH = "<math><ml:placeholder /></math>"


def plot_xml(rid: int, p: Plot, top: float, left: float) -> str:
    w, h = p.size
    traces = "".join(
        '<trace resultRef="%d"><traceStyle color="%s" symbol="none" line-weight="%d" '
        'line-style="Solid">lines</traceStyle></trace>' % (rid, c, wt)
        for _, c, wt in p.traces)
    xeq = "".join("<plotEquation><math>%s</math>%s</plotEquation>"
                  % (to_xml(parse_expr(e)), PH) for e in p.x)
    yeq = "".join("<plotEquation><math>%s</math>%s</plotEquation>"
                  % (to_xml(parse_expr(e)), PH) for e, _, _ in p.traces)

    def dom(limits):
        if limits is None:
            return ('<xyDomain scale-type="linear" auto-scale="true">'
                    '<startValue><ml:placeholder /></startValue>'
                    '<endValue><ml:placeholder /></endValue></xyDomain>')
        a, b = limits
        return ('<xyDomain scale-type="linear" auto-scale="false">'
                '<startValue><ml:real>%s</ml:real></startValue>'
                '<endValue><ml:real>%s</ml:real></endValue></xyDomain>' % (a, b))

    return (
        '<region region-id="%d" height="%s" width="%s" actualWidth="%s" actualHeight="%s" '
        'top="%s" left="%s">'
        '<plot origin-positioning="true" show-axis-expressions="false"><xyPlot>'
        '<legend display="false" show-border="false" position="below">'
        '<legend-label-items><legend-label-item /></legend-label-items></legend>'
        '<traces>%s</traces>'
        '<graph-size width="%s" height="%s" />'
        '<plotTitle display="false" show-border="false" title-position="PlotBoundaryTop" '
        'is-vertical="false" />'
        '<axes>'
        '<xAxis rank="1" legend-position="PlotBoundaryBottom">'
        '<axisLine position="origin" positionticmark="0" legendWidth="68.25" />'
        '<axisGrid><gridFrequency>8</gridFrequency><gridLabels display="true" />'
        '<gridLines display="false" color="#FFA1A3A6" /><tickMarks display="true" /></axisGrid>'
        '<axisLabel display="false" show-border="false" title-position="PlotBoundaryTop" '
        'is-vertical="false" />'
        '<markers /><plotEquations>%s</plotEquations>%s</xAxis>'
        '<yAxis rank="1" legend-position="PlotBoundaryRight">'
        '<axisLine position="origin" positionticmark="0" legendWidth="80.03" />'
        '<axisGrid><gridFrequency>8</gridFrequency><gridLabels display="true" />'
        '<gridLines display="false" color="#FFA1A3A6" /><tickMarks display="true" /></axisGrid>'
        '<axisLabel display="false" show-border="false" title-position="PlotBoundaryTop" '
        'is-vertical="true" />'
        '<markers /><plotEquations>%s</plotEquations>%s</yAxis>'
        '</axes></xyPlot></plot></region>'
        % (rid, h, w, w, h, top, left, traces, w - 19.0, h - 19.0,
           xeq, dom(p.xlim), yeq, dom(p.ylim)))


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------
def _place(top: float, height: float) -> float:
    """Push a region down if it would straddle a page break."""
    page = int(top // PAGE_H)
    page_end = (page + 1) * PAGE_H
    if top + height > page_end - PAGE_MARGIN:
        return page_end + 20.0
    return top


PRINT_W = 636.0         # usable width of an A4 page, in worksheet units
# A3 шире A4 на 328.8 единицы; листы для картинок печатаются на A3, чтобы
# график занимал больше пикселей при том же масштабе Prime
PAPER_EXTRA = {"A4": 0.0, "A3": 328.8}


def printable_width(paper: str = "A4") -> float:
    return PRINT_W + PAPER_EXTRA.get(paper, 0.0)


WRITES_FILE = re.compile(r"^(WRITE|APPEND)[A-Z]*\s*\(")


def _parse_source(source: str, figure_of: int | None, fig_size: tuple):
    """First pass: source lines -> ('row', cells) / ('plot', p) / ('gap', dy)."""
    items = []
    plot_no = 0
    for raw in source.splitlines():
        s = strip_comment(raw).strip()
        if not s:
            continue
        if s.startswith("@note"):
            items.append(("note", s[len("@note"):].strip()))
        elif s.startswith("@text"):
            items.append(("text", s[len("@text"):].strip()))
        elif s.startswith("@gap"):
            items.append(("gap", float(s.split()[1])))
        elif s.startswith("@plot"):
            p = parse_plot(s)
            plot_no += 1
            if figure_of is None:
                items.append(("plot", p))
            elif plot_no == figure_of:
                p.size = (fig_size[0], fig_size[1])
                items.append(("figplot", p))
        else:
            cells = split_top(s, ";")
            if figure_of is not None:
                # лист для картинки пересчитывается, а запись в файл перезаписала
                # бы результаты рабочего листа новой реализацией шума
                cells = [c for c in cells if not WRITES_FILE.match(c.strip())]
            if cells:
                items.append(("row", [math_region(c) for c in cells]))
    return items


def build_worksheet(source: str, figure_of: int | None = None,
                    fig_size: tuple | None = None,
                    measured: dict | None = None,
                    paper: str = "A4") -> tuple[str, list[dict]]:
    """Returns (worksheet.xml, manifest) where manifest describes each region.

    `figure_of` builds a capture variant instead of the real worksheet:
    0 keeps only the math (for a picture of the calculation), n >= 1 keeps the
    math plus the n-th plot, enlarged and parked at the bottom of the last
    page -- which is exactly where Ctrl+End lands, so the figure can be
    screenshotted without hunting for it.
    """
    print_w = printable_width(paper)
    if fig_size is None:
        fig_size = (print_w - LEFT0, 420.0 if paper == "A3" else 340.0)
    items = _parse_source(source, figure_of, fig_size)

    # Replace the width estimates with Prime's own measurements when a previous
    # pass supplied them: only Prime knows how wide a result renders.
    if measured:
        k = 0
        for kind, payload in items:
            if kind != "row":
                continue
            for cell in payload:
                if k in measured:
                    cell.width, cell.height = measured[k]
                k += 1

    # Column grid: a column is as wide as its widest cell, so formulas in
    # different rows line up. Falls back to per-row packing if that overflows.
    col_w: list[float] = []
    for kind, payload in items:
        if kind != "row":
            continue
        for i, cell in enumerate(payload):
            if i >= len(col_w):
                col_w.append(cell.width)
            else:
                col_w[i] = max(col_w[i], cell.width)

    avail = print_w - LEFT0
    grid_total = sum(col_w) + COL_GAP * max(0, len(col_w) - 1)
    use_grid = grid_total <= avail

    col_x = []
    x = LEFT0
    for w in col_w:
        col_x.append(x)
        x += w + COL_GAP

    regions_xml: list[str] = []
    manifest: list[dict] = []
    rid = 0
    top = TOP0
    pending_plot = None
    # In figure mode the formulas only need to compute, so they are packed
    # tightly: that keeps the plot high enough to be fully on screen at once.
    row_gap = 6.0 if figure_of else ROW_GAP

    parts: dict[str, bytes] = {}
    rels: list[tuple[str, str]] = []

    for kind, payload in items:
        if kind == "note":
            # explanation for the cheat sheet only, never placed on the sheet
            manifest.append({"kind": "note", "src": payload})
            continue

        if kind == "gap":
            top += payload
            continue

        if kind == "text":
            h = textblock.height_for(payload)
            top = _place(top, h)
            xml, rel_id, part, data = textblock.region(
                rid, payload, top, LEFT0, print_w - LEFT0, len(parts) + 1)
            regions_xml.append(xml)
            parts[part] = data
            rels.append((rel_id, part))
            manifest.append({"region": rid, "kind": "text", "src": payload,
                             "top": top, "left": LEFT0,
                             "width": print_w - LEFT0, "height": h})
            rid += 1
            top += h + 14.0
            continue

        if kind == "plot":
            p = payload
            if p is None:
                continue
            top = _place(top, p.size[1])
            regions_xml.append(plot_xml(rid, p, top, LEFT0))
            manifest.append({"region": rid, "kind": "plot", "src": p.src,
                             "top": top, "left": LEFT0,
                             "width": p.size[0], "height": p.size[1],
                             "traces": len(p.traces)})
            rid += 1
            top += p.size[1] + row_gap
            continue

        if kind == "figplot":
            pending_plot = payload
            continue

        cells = payload

        if use_grid:
            lines = [[(c, col_x[i], col_w[i]) for i, c in enumerate(cells)]]
        else:
            # Разложить ряд по ширине листа, перенося лишние регионы на
            # следующую строку: лист должен печататься целиком.
            lines, cur, x = [], [], LEFT0
            for c in cells:
                if cur and x + c.width > LEFT0 + avail:
                    lines.append(cur)
                    cur, x = [], LEFT0
                cur.append((c, x, c.width))
                x += c.width + COL_GAP
            if cur:
                lines.append(cur)

        for line_cells in lines:
            row_h = max(c.height for c, _, _ in line_cells)
            # Prime anchors a formula on its baseline, so fractions, powers and
            # tall brackets stick out above `top`; reserve that overhang.
            top = _place(top + max(0.0, row_h - BASE_H), row_h)
            for c, left, width in line_cells:
                regions_xml.append(
                    '<region region-id="%d" actualWidth="%s" actualHeight="%s" top="%s" left="%s">'
                    '<math resultRef="%d">%s%s</math></region>'
                    % (rid, round(width, 2), c.height, top, round(left, 2), rid, c.xml, RESFMT))
                manifest.append({"region": rid, "kind": "math", "src": c.src,
                                 "top": top, "left": round(left, 2),
                                 "width": round(width, 2), "height": c.height})
                rid += 1
            end = line_cells[-1][1] + line_cells[-1][2]
            if end > print_w + LEFT0 + 1:
                manifest[-1]["warning"] = "регион шире страницы (%.0f)" % end
            top += row_h + row_gap

    if pending_plot is not None:
        p = pending_plot
        ptop = top + 10.0
        p.size = (p.size[0], max(220.0, min(p.size[1], VISIBLE_H - ptop)))
        regions_xml.append(plot_xml(rid, p, ptop, LEFT0))
        manifest.append({"region": rid, "kind": "plot", "src": p.src, "top": ptop,
                         "left": LEFT0, "width": p.size[0], "height": p.size[1],
                         "traces": len(p.traces), "figure": True})

    return HEAD + "".join(regions_xml) + TAIL, manifest, {"parts": parts, "rels": rels}
