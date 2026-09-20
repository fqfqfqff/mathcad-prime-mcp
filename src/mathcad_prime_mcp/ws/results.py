"""Read a calculated .mcdx back as text: formulas, values, errors, plot ranges.

This is what replaces screenshots. Mathcad stores every computed result in
mathcad/result.xml keyed by the resultRef of the region that produced it, and
stores calculation errors there too, with the same message the GUI shows.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .expr import from_xml
from .pack import RESULTS, WORKSHEET, read_part

WS = "{http://schemas.mathsoft.com/worksheet50}"
ML = "{http://schemas.mathsoft.com/math50}"
RS = "{http://schemas.mathsoft.com/result10}"


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x
    return int(v) if v == int(v) and abs(v) < 1e15 else v


def _fmt(v) -> str:
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "%.6g" % v
    if isinstance(v, dict) and "rows" in v:
        head = ", ".join(_fmt(x) for x in v["head"])
        tail = ", ".join(_fmt(x) for x in v["tail"])
        size = "%d×%d" % (v["rows"], v["cols"])
        if v["min"] is None:
            return "вектор %s" % size
        return "вектор %s [%s ... %s], от %s до %s" % (
            size, head, tail, _fmt(v["min"]), _fmt(v["max"]))
    return str(v)


# --------------------------------------------------------------------------
def _parse_results(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    out = {}
    for rd in root:
        rid = rd.get("result-id")
        if rid is None:
            continue
        entry = {"status": rd.get("calculation-status")}
        for child in rd:
            tag = child.tag.split("}")[-1]
            if tag == "result":
                kids = list(child)
                if len(kids) == 1 and kids[0].tag == ML + "real":
                    entry["value"] = _f(kids[0].text)
                elif kids and kids[0].tag == ML + "matrix":
                    mx = kids[0]
                    vals = [_f(e.text) for e in mx if e.tag == ML + "real"]
                    entry["value"] = {"rows": int(mx.get("rows", 0)),
                                      "cols": int(mx.get("cols", 0)),
                                      "head": vals[:5], "tail": vals[-3:] if vals else [],
                                      "min": min(vals) if vals else None,
                                      "max": max(vals) if vals else None}
                else:
                    entry["value"] = from_xml(kids[0]) if kids else None
            elif tag == "engineErrors":
                errs = []
                for e in child:
                    # дети лежат в пространстве имён result10, искать по голому
                    # имени нельзя
                    kids = {c.tag.split("}")[-1]: (c.text or "") for c in e}
                    msg = kids.get("resource-string", "")
                    code = kids.get("errorCode", "")
                    errs.append({"code": code.split("\t")[0], "text": msg.strip(),
                                 "derived": e.get("previous-error-id") is not None})
                entry["errors"] = errs
            elif tag == "Warnings":
                entry["warnings"] = [w.get("nodeId", "") for w in child]
            elif tag == "Trace2dResult":
                ri = child.find(ML + "RangeInfo")
                res = child.find(ML + "ResultInfo")
                entry["trace"] = {
                    "x": [_f(ri.get("Min")), _f(ri.get("Max"))] if ri is not None else None,
                    "y": [_f(res.get("Min")), _f(res.get("Max"))] if res is not None else None,
                }
        out[int(rid)] = entry
    return out


def text_blocks(path: str) -> dict:
    """{item-idref: текст} для всех текстовых блоков документа.

    Содержимое лежит не в worksheet.xml, а в отдельных частях-архивах,
    на которые ссылается worksheet.xml.rels.
    """
    import io
    import re
    import zipfile

    rels = read_part(path, "mathcad/_rels/worksheet.xml.rels")
    if not rels:
        return {}
    out = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for m in re.finditer(r"<Relationship\b[^>]*>", rels):
            tag = m.group(0)
            tid = re.search(r'Id="([^"]+)"', tag)
            tgt = re.search(r'Target="([^"]+)"', tag)
            if not tid or not tgt:
                continue
            part = tgt.group(1).lstrip("/")
            if part not in names:
                continue
            try:
                inner = zipfile.ZipFile(io.BytesIO(z.read(part)))
                xaml = inner.read("Xaml/Document.xaml").decode("utf-8")
            except Exception:
                continue
            runs = re.findall(r"<Run[^>]*>(.*?)</Run>", xaml, re.S)
            text = "".join(runs).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            out[tid.group(1)] = text.strip()
    return out


def _region_key(reg):
    try:
        return (float(reg.get("top", 0)), float(reg.get("left", 0)))
    except ValueError:
        return (0.0, 0.0)


def _parse_worksheet(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    regions = root.find(WS + "regions")
    if regions is None:
        regions = root.find("regions")
    out = []
    for reg in sorted(list(regions), key=_region_key):
        rid = reg.get("region-id")
        math = reg.find(WS + "math") or reg.find("math")
        plot = reg.find(WS + "plot") or reg.find("plot")
        text = reg.find(WS + "text") or reg.find("text")
        if text is not None:
            out.append({"region": rid, "kind": "text",
                        "ref_id": text.get("item-idref", ""),
                        "top": _f(reg.get("top")), "left": _f(reg.get("left"))})
        elif math is not None:
            body = [c for c in math if c.tag.startswith(ML)]
            text = from_xml(body[0]) if body else "?"
            out.append({"region": rid, "kind": "math", "ref": int(math.get("resultRef", -1)),
                        "text": text, "top": _f(reg.get("top")), "left": _f(reg.get("left"))})
        elif plot is not None:
            xy = plot.find(WS + "xyPlot") or plot.find("xyPlot")
            refs, xs, ys = [], [], []
            if xy is not None:
                for tr in xy.iter():
                    if tr.tag.split("}")[-1] == "trace" and tr.get("resultRef"):
                        refs.append(int(tr.get("resultRef")))
                for ax_name, bucket in (("xAxis", xs), ("yAxis", ys)):
                    for ax in xy.iter():
                        if ax.tag.split("}")[-1] != ax_name:
                            continue
                        for pe in ax.iter():
                            if pe.tag.split("}")[-1] != "plotEquation":
                                continue
                            maths = [m for m in pe if m.tag.split("}")[-1] == "math"]
                            if maths and len(list(maths[0])):
                                bucket.append(from_xml(list(maths[0])[0]))
            out.append({"region": rid, "kind": "plot", "refs": refs, "x": xs, "y": ys,
                        "top": _f(reg.get("top")), "left": _f(reg.get("left")),
                        "width": _f(reg.get("width")), "height": _f(reg.get("height"))})
    return out


# --------------------------------------------------------------------------
def inspect(path: str) -> dict:
    """Full structured view of a worksheet: every region with its result."""
    ws_xml = read_part(path, WORKSHEET)
    if ws_xml is None:
        raise FileNotFoundError(f"{path}: нет {WORKSHEET}")
    res_xml = read_part(path, RESULTS)
    results = _parse_results(res_xml) if res_xml else {}

    regions = _parse_worksheet(ws_xml)
    texts = text_blocks(path)
    errors, warnings, rows = [], [], []

    for r in regions:
        if r["kind"] == "text":
            rows.append({"region": r["region"], "kind": "text",
                         "text": texts.get(r.get("ref_id", ""), "")})
        elif r["kind"] == "math":
            info = results.get(r["ref"], {})
            row = {"region": r["region"], "text": r["text"]}
            if "value" in info:
                row["value"] = info["value"]
            if info.get("errors"):
                primary = [e for e in info["errors"] if not e["derived"]] or info["errors"]
                row["error"] = primary[0]["text"]
                row["error_code"] = primary[0]["code"]
                errors.append({"region": r["region"], "text": r["text"],
                               "error": primary[0]["text"], "code": primary[0]["code"],
                               "derived": all(e["derived"] for e in info["errors"])})
            if info.get("warnings"):
                warnings.append({"region": r["region"], "text": r["text"],
                                 "warning": ", ".join(info["warnings"])})
            rows.append(row)
        else:
            ranges = []
            for ref in r["refs"]:
                tr = results.get(ref, {}).get("trace")
                if tr:
                    ranges.append(tr)
                elif results.get(ref, {}).get("errors"):
                    errors.append({"region": r["region"], "text": "plot",
                                   "error": results[ref]["errors"][0]["text"],
                                   "code": results[ref]["errors"][0]["code"],
                                   "derived": False})
            rows.append({"region": r["region"], "kind": "plot",
                         "x": r["x"], "y": r["y"], "ranges": ranges,
                         "geometry": {"top": r["top"], "left": r["left"],
                                      "width": r["width"], "height": r["height"]}})

    calculated = any("value" in row or row.get("kind") == "plot" for row in rows)
    return {"path": path, "regions": len(regions), "rows": rows,
            "errors": errors, "warnings": warnings, "calculated": calculated}


def measured_values(path: str) -> dict:
    """Значения math-регионов в порядке документа, ключи те же, что у
    measured_sizes."""
    ws_xml = read_part(path, WORKSHEET)
    res_xml = read_part(path, RESULTS)
    if ws_xml is None or res_xml is None:
        return {}
    res = _parse_results(res_xml)
    out, idx = {}, 0
    for r in _parse_worksheet(ws_xml):
        if r["kind"] != "math":
            continue
        out[idx] = res.get(r["ref"], {}).get("value")
        idx += 1
    return out


def measured_sizes(path: str) -> dict:
    """Prime's own rendered size of each math region, in document order.

    Only Prime knows how wide a region ends up once its result is displayed,
    so a second layout pass uses these instead of estimates.
    """
    ws_xml = read_part(path, WORKSHEET)
    if ws_xml is None:
        return {}
    root = ET.fromstring(ws_xml)
    regions = root.find(WS + "regions")
    if regions is None:
        regions = root.find("regions")
    out, idx = {}, 0
    for reg in sorted(list(regions), key=_region_key):
        math = reg.find(WS + "math") or reg.find("math")
        if math is None:
            continue
        try:
            out[idx] = (float(reg.get("actualWidth")), float(reg.get("actualHeight")))
        except (TypeError, ValueError):
            pass
        idx += 1
    return out


_VEC_RE = None


def trace_data(path: str, region: str | int) -> list[dict]:
    """Numeric x/y data of every trace of one plot region.

    Mathcad stores the plotted points in result.xml, so curves can be analysed
    (peak positions, RMS, ...) without re-deriving them outside the worksheet.
    """
    import re

    ws_xml = read_part(path, WORKSHEET)
    res_xml = read_part(path, RESULTS)
    if ws_xml is None or res_xml is None:
        raise FileNotFoundError(f"{path}: нет данных, сначала пересчитать")

    target = str(region)
    refs, labels = [], []
    for r in _parse_worksheet(ws_xml):
        if r["kind"] == "plot" and str(r["region"]) == target:
            refs = r["refs"]
            labels = r["y"]
            break
    if not refs:
        raise ValueError(f"регион {region} не является графиком с данными")

    out = []
    for i, ref in enumerate(refs):
        start = res_xml.find('result-id="%d"' % ref)
        if start < 0:
            continue
        end = res_xml.find("</resultData>", start)
        block = res_xml[start:end]
        vecs = re.findall(r"<ml:DataVectors[^>]*>\[([^\]]*)\]", block)
        if len(vecs) < 2:
            continue
        xs = [float(v) for v in vecs[0].split(",") if v]
        ys = [float(v) for v in vecs[1].split(",") if v]
        out.append({"trace": i, "label": labels[i] if i < len(labels) else "",
                    "n": len(ys), "x": xs, "y": ys})
    return out


def as_text(report: dict) -> str:
    """One compact line per region -- the form meant to be read instead of a screenshot."""
    lines = []
    for row in report["rows"]:
        if row.get("kind") == "text":
            lines.append("%3s  # %s" % (row["region"], row.get("text") or "текстовый блок"))
            continue
        if row.get("kind") == "plot":
            rng = "; ".join("x[%s..%s] y[%s..%s]"
                            % (_fmt(t["x"][0]), _fmt(t["x"][1]),
                               _fmt(t["y"][0]), _fmt(t["y"][1]))
                            for t in row["ranges"] if t.get("x") and t.get("y"))
            lines.append("%3s  график  x=%s  y=%s   %s"
                         % (row["region"], ",".join(row["x"]), ",".join(row["y"]), rng))
            continue
        line = "%3s  %s" % (row["region"], row["text"])
        if "value" in row:
            line += "  ->  %s" % _fmt(row["value"])
        if "error" in row:
            line += "   ### ОШИБКА: %s" % row["error"]
        lines.append(line)
    head = "%s  регионов: %d, ошибок: %d, предупреждений: %d%s" % (
        report["path"], report["regions"], len(report["errors"]), len(report["warnings"]),
        "" if report["calculated"] else "  (НЕ РАССЧИТАН)")
    return head + "\n" + "\n".join(lines)
