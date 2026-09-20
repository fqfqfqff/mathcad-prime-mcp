"""Worksheet authoring tools: write a .mcdx from compact source, calculate it
and read the results back as text instead of looking at screenshots."""

from __future__ import annotations

import os
from typing import Any

from .ws import doc, drive, pack, results

SOURCE_HELP = """Формат исходника (строка = ряд регионов, ';' разделяет регионы в ряду):

    # комментарий
    S := READWAV("f.wav") ; L := length(S)      # определения
    n := 0..L-1 ; D[n] := rnd(m)-m/2            # диапазон и индексный ряд
    WRITEWAV("out.wav",44100,16,V)=             # вычисление (показать значение)
    L= ; sk=                                    # вывод значений
    @plot x=n y=S[n]:red:2,V[n]:navy:1 xlim=0..200 ylim=0..5 size=620x260
    @text 1. Зашумление: m это амплитуда помехи  # подпись прямо в листе
    @note sk - СКО шума, по нему считается порог # только в шпаргалку
    @gap 40                                     # дополнительный отступ

Выражения: + - * / ^, |x| модуль, f(a,b) вызов, X[i] индекс, a..b диапазон.
Суммы и произведения: sum(k, 0..N-1, x[k]*y[n-k]), prod(i, 1..N, i).
Греческие имена по ASCII-псевдонимам: Phi (Хевисайд), pi, sigma, omega, Delta.
Слева от ':=' допустимо имя, X[i] или f(x). Ряды позиционируются сами и не
разрываются границей страницы."""


def _read_source(source: str) -> str:
    """Accept either the source text itself or a path to a .mcd file."""
    if "\n" not in source and source.lower().endswith(".mcd") and os.path.exists(source):
        with open(source, encoding="utf-8") as fh:
            return fh.read()
    return source


def register(mcp, get_app):
    @mcp.tool()
    def mathcad_source_help() -> str:
        """Синтаксис компактного исходника для mathcad_write."""
        return SOURCE_HELP

    @mcp.tool()
    def mathcad_write(source: str, path: str, calculate: bool = True,
                      tidy: bool = True) -> dict:
        """Собрать .mcdx из компактного исходника (текст или путь к .mcd).

        При calculate=True открывает лист в Prime, пересчитывает, сохраняет и
        возвращает проверку: значения, ошибки, диапазоны графиков.
        При tidy=True делает второй проход: забирает у Prime фактические
        размеры регионов (ширина формулы вместе с показанным результатом
        известна только ему) и заново раскладывает всё ровными колонками.
        Синтаксис исходника: mathcad_source_help.
        """
        text = _read_source(source)
        xml, manifest, extra = doc.build_worksheet(text)
        app = get_app() if calculate else None
        if app is not None:
            drive.close_if_open(app, path)
        pack.write_mcdx(xml, path, parts=extra["parts"], rels=extra["rels"])
        out: dict[str, Any] = {"path": os.path.abspath(path), "regions": len(manifest)}
        if app is None:
            return out

        drive.calculate(app, path)
        if tidy:
            sizes = results.measured_sizes(path)
            if sizes:
                xml, manifest, extra = doc.build_worksheet(text, measured=sizes)
                drive.close_if_open(app, path)
                pack.write_mcdx(xml, path, parts=extra["parts"], rels=extra["rels"])
                drive.calculate(app, path)

        warn = [m for m in manifest if "warning" in m]
        if warn:
            out["layout_warnings"] = [f"{m['src']}: {m['warning']}" for m in warn]
        report = results.inspect(path)
        out["report"] = results.as_text(report)
        out["errors"] = len(report["errors"])
        return out

    @mcp.tool()
    def mathcad_verify(path: str, recalculate: bool = True) -> str:
        """Пересчитать лист и вернуть его целиком текстом: каждая формула,
        её значение, ошибки, диапазоны данных на графиках.

        Это замена скриншотам: ошибки приходят с тем же текстом, который
        показывает Prime. С recalculate=False читает то, что уже в файле.
        """
        if recalculate:
            drive.calculate(get_app(), path)
        return results.as_text(results.inspect(path))

    @mcp.tool()
    def mathcad_trace_data(path: str, region: str, stride: int = 1,
                           limit: int = 400) -> dict:
        """Числовые данные кривых одного графика (для расчёта выводов отчёта).

        region берётся из вывода mathcad_verify. stride прореживает, limit
        ограничивает число возвращаемых точек на кривую.
        """
        traces = results.trace_data(path, region)
        out = []
        for t in traces:
            xs, ys = t["x"][::stride][:limit], t["y"][::stride][:limit]
            out.append({"label": t["label"], "n": t["n"],
                        "ymin": min(t["y"]), "ymax": max(t["y"]),
                        "x": xs, "y": ys})
        return {"region": region, "traces": out}

    @mcp.tool()
    def mathcad_report(source: str, out_docx: str, pdf: bool = False,
                       base_dir: str = "") -> dict:
        """Собрать отчёт по лабораторной из markdown в .docx по ГОСТ.

        Times New Roman 14, полуторный интервал, поля 30/15/20/20 мм,
        титульный лист из front matter, картинки масштабируются по ширине
        страницы сами. Разметка сверх markdown: `$$формула$$` отдельной
        строкой по центру, `![подпись](file.png)` картинка с подписью,
        `X_{i}` и `10^{5}` подстрочный и надстрочный, `\\newpage` разрыв.

        source это текст или путь к .md. Пути картинок считаются от base_dir
        (по умолчанию папка отчёта).
        """
        from .ws import report

        text = source
        if "\n" not in source and source.lower().endswith(".md") and os.path.exists(source):
            with open(source, encoding="utf-8") as fh:
                text = fh.read()
            base_dir = base_dir or os.path.dirname(os.path.abspath(source))

        path = report.render(text, out_docx, base_dir=base_dir)
        out = {"docx": path}
        if pdf:
            out["pdf"] = report.to_pdf(path)
        return out

    @mcp.tool()
    def mathcad_cheatsheet(source: str, path: str, out_md: str,
                           title: str = "Шпаргалка к защите") -> dict:
        """Собрать памятку к защите: каждая формула листа с её значением и
        пояснением из строк @note исходника.

        Пояснения живут только в памятке, в самом .mcdx их нет, поэтому
        сдаваемый документ остаётся чистым. Подписи, которые нужны прямо в
        листе, пишутся через @text.
        """
        text = _read_source(source)
        notes = {}
        order = []
        for line in text.splitlines():
            s = line.strip()
            if not s.startswith("@note"):
                continue
            body = s[len("@note"):].strip()
            key, _, expl = body.partition(" - ")
            key = key.strip()
            notes[key] = expl.strip() or body
            order.append(key)

        report = results.inspect(path)
        lines = ["# %s" % title, ""]
        for row in report["rows"]:
            if row.get("kind") == "text":
                continue
            if row.get("kind") == "plot":
                lines.append("- **график** `%s` от `%s`" %
                             (", ".join(row["y"]), ", ".join(dict.fromkeys(row["x"]))))
                continue
            name = row["text"].split(":=")[0].strip().rstrip("=").strip()
            piece = "- `%s`" % row["text"]
            if "value" in row:
                piece += " = **%s**" % results._fmt(row["value"])
            expl = notes.get(name) or notes.get(name.split("[")[0])
            if expl:
                piece += ":  %s" % expl
            lines.append(piece)

        unused = [k for k in order if k not in
                  {r.get("text", "").split(":=")[0].strip().rstrip("=").strip()
                   for r in report["rows"]}]
        if unused:
            lines += ["", "## Прочее"] + ["- %s: %s" % (k, notes[k]) for k in unused]

        os.makedirs(os.path.dirname(os.path.abspath(out_md)) or ".", exist_ok=True)
        with open(out_md, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return {"path": os.path.abspath(out_md), "notes": len(notes)}

    @mcp.tool()
    def mathcad_figures(source: str, out_dir: str, work_dir: str = "",
                        zoom_clicks: int = 0) -> dict:
        """Сделать картинки для отчёта: расчётная часть и каждый график
        отдельным png, обрезанным по содержимому.

        Для каждой картинки собирается вспомогательный лист, где нужный график
        стоит внизу последней страницы, поэтому Ctrl+End всегда показывает
        именно его. Скриншоты сохраняются файлами, в ответ идут только пути.

        work_dir должен быть папкой рабочего .mcdx, если формулы читают файлы
        по относительному имени (READWAV, READPRN).
        """
        import time

        import psutil
        from pywinauto import Application

        from .ws import shots

        text = _read_source(source)
        n_plots = sum(1 for line in text.splitlines()
                      if line.strip().startswith("@plot"))
        work_dir = work_dir or os.path.abspath(out_dir)
        os.makedirs(work_dir, exist_ok=True)

        app = get_app()
        try:
            app.Visible = True          # capturing needs a window on screen
        except Exception:
            pass

        def top_window():
            for _ in range(8):
                pids = [p.pid for p in psutil.process_iter(["name"])
                        if p.info["name"] == "MathcadPrime.exe"]
                if pids:
                    try:
                        return Application(backend="uia").connect(
                            process=pids[0]).top_window(), pids[0]
                    except Exception:
                        pass
                time.sleep(1.0)
            raise RuntimeError("окно Mathcad Prime не найдено")

        files, notes, made = [], [], []
        zoomed = False
        for idx in range(0, n_plots + 1):
            xml, man, extra = doc.build_worksheet(text, figure_of=idx)
            spot = next((m for m in man if m.get("figure")), None)
            wpath = os.path.join(work_dir, "_fig%d.mcdx" % idx)
            drive.close_if_open(app, wpath)
            pack.write_mcdx(xml, wpath, parts=extra["parts"], rels=extra["rels"])
            ws = drive.calculate(app, wpath)
            try:
                ws.Activate()
            except Exception:
                pass
            time.sleep(0.8)

            # a previous figure is closed only now, so Prime always keeps a window
            for old in made:
                drive.close_if_open(app, old)
            made = [wpath]

            win, pid = top_window()
            shots.ensure_focus(win, pid)

            # Prime keeps other documents open in tabs; make sure the one being
            # captured is the one on screen, checking by the window title.
            want = os.path.basename(wpath)
            for _ in range(10):
                if want.lower() in (win.window_text() or "").lower():
                    break
                try:
                    ws.Activate()
                except Exception:
                    pass
                time.sleep(0.7)
            else:
                notes.append("не удалось вывести на экран %s" % want)
            if zoom_clicks and not zoomed:
                shots.zoom_in(win, zoom_clicks)
                zoomed = True
            name = "doc.png" if idx == 0 else "plot%d.png" % idx
            out_png = os.path.join(out_dir, name)
            if idx == 0:
                shots.goto(win, "home", pid=pid)
                got = shots.capture_view(win, out_png)
            elif spot:
                got = shots.capture_region(win, out_png, spot["top"], spot["left"],
                                           spot["width"], spot["height"], pid=pid)
            else:
                got = shots.capture_best(win, out_png, pid=pid)
            if got:
                files.append(got)
            else:
                notes.append("не удалось выделить картинку %s" % name)

        for idx in range(0, n_plots + 1):
            drive.close_if_open(app, os.path.join(work_dir, "_fig%d.mcdx" % idx))
        for idx in range(0, n_plots + 1):
            try:
                os.remove(os.path.join(work_dir, "_fig%d.mcdx" % idx))
            except OSError:
                pass

        return {"files": files, "plots": n_plots, "notes": notes,
                "work_dir": work_dir}
