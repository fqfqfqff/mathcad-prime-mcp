"""Driving Mathcad Prime through COM for the build -> calculate -> read loop."""

from __future__ import annotations

import os
import time


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def find_open(app, path: str):
    """Return the already-open worksheet for `path`, if Prime has it."""
    target = norm(path)
    for i in range(app.Worksheets.Count):
        ws = app.Worksheets.Item(i)
        try:
            if norm(ws.FullName) == target:
                return ws
        except Exception:
            continue
    return None


def close_if_open(app, path: str) -> bool:
    ws = find_open(app, path)
    if ws is None:
        return False
    for args in ((2,), (0,), ()):
        try:
            ws.Close(*args)
            return True
        except Exception:
            continue
    return False


def open_worksheet(app, path: str):
    ws = find_open(app, path)
    if ws is not None:
        return ws
    opened = app.Open(os.path.abspath(path))
    # Open() hands back a loosely typed wrapper; re-fetch it from the collection
    # so the full IMathcadPrimeWorksheet interface (Activate, ...) is available.
    ws = find_open(app, path) or opened
    if ws is None:
        raise RuntimeError(
            f"Prime отказался открыть {path}: обычно это значит, что worksheet.xml "
            f"не проходит его схему (неизвестный тег или неверное число потомков)")
    return ws


def calculate(app, path: str, settle: float = 0.5):
    """Recalculate and save, so results land in the file for reading."""
    ws = open_worksheet(app, path)
    ws.Synchronize()
    if settle:
        time.sleep(settle)
    ws.Save()
    if settle:
        time.sleep(settle)
    return ws
