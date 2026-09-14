"""Capturing report figures from a worksheet without hunting for them by eye.

A separate "figures" worksheet is generated from the same source with one plot
per page, so every figure can be reached with a fixed number of PgDn presses
and cropped mechanically: the page sheet is found by colour, and the figure by
the bounding box of ink (the light blue grid is deliberately below threshold).
"""

from __future__ import annotations

import os
import time

SHEET_MIN = 200      # a page pixel has R,G,B all above this
INK_MAX = 190        # ink (axes, labels, curves) is darker than the grid
PAD = 10


def _pil(win):
    """Capture the window itself, not the screen area it occupies.

    PrintWindow with PW_RENDERFULLCONTENT renders the window even when another
    window overlaps it, which capture_as_image() cannot do.
    """
    try:
        import ctypes

        import win32con
        import win32gui
        import win32ui
        from PIL import Image

        hwnd = win.handle
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        src_dc = win32gui.GetWindowDC(hwnd)
        dc = win32ui.CreateDCFromHandle(src_dc)
        mem_dc = dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(dc, w, h)
        mem_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               bits, "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        mem_dc.DeleteDC()
        dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, src_dc)
        if ok and img.convert("L").getextrema()[1] > 30:
            return img
    except Exception:
        pass
    return win.capture_as_image()


def ensure_focus(win, pid: int, tries: int = 6) -> None:
    """Keyboard input only goes to the foreground window, so verify it."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    for _ in range(tries):
        try:
            win.set_focus()
        except Exception:
            pass
        time.sleep(0.6)
        hwnd = user32.GetForegroundWindow()
        got = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value == pid:
            return
    raise RuntimeError("окно Mathcad не удалось вывести на передний план")


def _row_edges(px, w: int, y: int, bridge: int = 80):
    """Longest light span on one row, bridging thin dark gaps.

    Gaps are bridged because curves and glyphs printed on the page are dark;
    the span must not merge with the window's light scrollbar, which sits far
    beyond the sheet, hence a bounded bridge.
    """
    spans = []
    start = None
    for x in range(w):
        r, g, b = px[x, y][:3]
        light = r > SHEET_MIN and g > SHEET_MIN and b > SHEET_MIN
        if light and start is None:
            start = x
        elif not light and start is not None:
            spans.append((start, x - 1))
            start = None
    if start is not None:
        spans.append((start, w - 1))

    merged = []
    for s, e in spans:
        if merged and s - merged[-1][1] <= bridge:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    if not merged:
        return None
    best = max(merged, key=lambda p: p[1] - p[0])
    if best[1] - best[0] < 100:
        return None
    return best


def find_sheet(img, top=200, bottom_margin=40) -> tuple[int, int]:
    """Horizontal extent of the page sheet.

    Sampled over many rows and reduced by median: a single row can cut through
    a plotted curve, which would otherwise truncate the detected page.
    """
    px = img.convert("RGB").load()
    w, h = img.size
    y0, y1 = top + 10, h - bottom_margin
    lefts, rights = [], []
    for i in range(20):
        y = y0 + (y1 - y0) * i // 20
        edges = _row_edges(px, w, y)
        if edges:
            lefts.append(edges[0])
            rights.append(edges[1])
    if not lefts:
        return 0, w
    lefts.sort()
    rights.sort()
    return lefts[len(lefts) // 2], rights[len(rights) // 2] + 1


def ink_bbox(img, x0: int, x1: int, y0: int, y1: int):
    """Bounding box of dark pixels inside the given window of the screenshot."""
    g = img.convert("L")
    px = g.load()
    minx, miny, maxx, maxy = x1, y1, x0, y0
    found = False
    for y in range(y0, y1):
        for x in range(x0, x1):
            if px[x, y] < INK_MAX:
                found = True
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    if not found:
        return None
    return (max(x0, minx - PAD), max(y0, miny - PAD),
            min(x1, maxx + PAD + 1), min(y1, maxy + PAD + 1))


def zoom_in(win, clicks: int):
    """Click the status bar '+' -- located relative to the window's bottom-right."""
    rect = win.rectangle()
    w, h = rect.width(), rect.height()
    for _ in range(clicks):
        win.click_input(coords=(w - 87, h - 20))
        time.sleep(0.45)


def focus_canvas(win, sheet_left: int):
    """Put the caret on empty page margin so PgDn scrolls instead of editing."""
    rect = win.rectangle()
    win.click_input(coords=(sheet_left + 12, rect.height() - 120))
    time.sleep(0.4)


def page_bands(img, x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int]]:
    """Split the view into page slices, using the dark gap between sheets."""
    px = img.convert("RGB").load()
    step = max(1, (x1 - x0) // 120)
    gap_rows = []
    for y in range(y0, y1):
        dark = total = 0
        for x in range(x0, x1, step):
            r, g, b = px[x, y][:3]
            total += 1
            if max(r, g, b) < 150:
                dark += 1
        gap_rows.append(total and dark / total > 0.7)

    bands, start = [], None
    for i, is_gap in enumerate(gap_rows):
        if not is_gap and start is None:
            start = i
        elif is_gap and start is not None:
            if i - start > 40:
                bands.append((y0 + start, y0 + i))
            start = None
    if start is not None and len(gap_rows) - start > 40:
        bands.append((y0 + start, y1))
    return bands or [(y0, y1)]


def ink_amount(img, x0, x1, y0, y1) -> int:
    g = img.convert("L").load()
    step = 3
    n = 0
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            if g[x, y] < INK_MAX:
                n += 1
    return n


def capture_view(win, out_path: str) -> str | None:
    """Crop whatever figure is currently on screen: pick the page slice holding
    the most ink, then trim to its content."""
    img = _pil(win)
    left, right = find_sheet(img)
    x0, x1 = left + 2, right - 2
    y0, y1 = 195, img.size[1] - 45
    bands = page_bands(img, x0, x1, y0, y1)
    best, best_ink = None, 0
    for by0, by1 in bands:
        ink = ink_amount(img, x0, x1, by0, by1)
        if ink > best_ink:
            best, best_ink = (by0, by1), ink
    if best is None or best_ink < 50:
        return None
    box = ink_bbox(img, x0, x1, best[0], best[1])
    if box is None:
        return None
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.crop(box).save(out_path)
    return out_path


def _view_figure(img):
    """Best page slice of the current view plus how much ink it holds."""
    left, right = find_sheet(img)
    x0, x1 = left + 2, right - 2
    y0, y1 = 195, img.size[1] - 45
    best, best_ink = None, 0
    for by0, by1 in page_bands(img, x0, x1, y0, y1):
        ink = ink_amount(img, x0, x1, by0, by1)
        if ink > best_ink:
            best, best_ink = (by0, by1), ink
    if best is None:
        return None, 0, None
    return ink_bbox(img, x0, x1, best[0], best[1]), best_ink, best


def capture_best(win, out_path: str, pid: int = 0, max_pages: int = 8,
                 settle: float = 1.0) -> str | None:
    """Page through the document and keep the view holding the most content.

    Prime always appends a trailing empty page, so paging blindly to the end
    lands on nothing; scanning avoids depending on the page count at all.
    """
    if pid:
        ensure_focus(win, pid)
    img = _pil(win)
    left, _ = find_sheet(img)
    focus_canvas(win, left)
    win.type_keys("^{HOME}")
    time.sleep(settle)

    # Collect every view, then choose: the figure is the last content of the
    # document, and a view showing it whole has margins above and below it,
    # unlike a view that merely clips its top or bottom edge.
    whole, clipped = [], []
    empty_streak = 0
    for _ in range(max_pages):
        img = _pil(win)
        box, ink, band = _view_figure(img)
        if box and ink >= 30:
            entry = (img, box, (box[2] - box[0]) * (box[3] - box[1]))
            if band and box[1] > band[0] + 2 and box[3] < band[1] - 2:
                whole.append(entry)
            else:
                clipped.append(entry)
            empty_streak = 0
        elif whole or clipped:
            empty_streak += 1
            if empty_streak >= 2:
                break
        win.type_keys("{PGDN}")
        time.sleep(settle)

    if whole:
        best_img, best_box, _ = whole[-1]
    elif clipped:
        best_img, best_box, _ = max(clipped, key=lambda e: e[2])
    else:
        return None
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    best_img.crop(best_box).save(out_path)
    return out_path


PAPER_W = 793.7          # A4 width in worksheet units
LEFT_MARGIN = 94.0       # printable area offset inside the sheet
TOP_MARGIN = 69.0


def sheet_rect(img):
    """(left, right, paper_top, scale) of the page sheet in the screenshot."""
    left, right = find_sheet(img)
    px = img.convert("RGB").load()
    xmid = (left + right) // 2
    paper_top = 195
    for y in range(195, img.size[1] - 60):
        r, g, b = px[xmid, y][:3]
        if r > SHEET_MIN and g > SHEET_MIN and b > SHEET_MIN:
            paper_top = y
            break
    return left, right, paper_top, (right - left) / PAPER_W


def capture_region(win, out_path: str, top: float, left: float,
                   width: float, height: float, pid: int = 0,
                   settle: float = 1.2) -> str | None:
    """Crop one region by its worksheet coordinates, with the view at the top.

    Deterministic: the sheet is located by colour, which fixes the scale and
    origin, so the region's own top/left/size decide the crop.
    """
    if pid:
        ensure_focus(win, pid)
    img = _pil(win)
    l, _r, paper_top, scale = sheet_rect(img)
    focus_canvas(win, l)
    win.type_keys("^{HOME}")
    time.sleep(settle)

    img = _pil(win)
    l, r, paper_top, scale = sheet_rect(img)
    x0 = int(l + (LEFT_MARGIN + left) * scale) - PAD
    y0 = int(paper_top + (TOP_MARGIN + top) * scale) - PAD
    x1 = int(x0 + width * scale) + 3 * PAD
    y1 = int(y0 + height * scale) + 3 * PAD
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.size[0], x1), min(img.size[1], y1)
    if x1 - x0 < 60 or y1 - y0 < 60:
        return None

    box = ink_bbox(img, x0, x1, y0, y1) or (x0, y0, x1, y1)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.crop(box).save(out_path)
    return out_path


def goto(win, where: str, pid: int = 0, settle: float = 1.2, pages: int = 14):
    """Scroll to the start or the end of the document.

    Ctrl+End is ignored by Prime's canvas, so the end is reached by paging down
    until it stops -- the document is only a few pages long.
    """
    if pid:
        ensure_focus(win, pid)
    img = _pil(win)
    left, _ = find_sheet(img)
    focus_canvas(win, left)
    win.type_keys("^{HOME}")
    time.sleep(settle)
    if where != "home":
        win.type_keys("{PGDN %d}" % pages)
        time.sleep(settle)
