"""MCP server wrapping PTC Mathcad Prime 12's COM Automation API
(Ptc.MathcadPrime.Automation, ProgID "MathcadPrime.Application").

Only variables explicitly marked as Input/Output variables inside a worksheet
(right-click a variable in Prime -> "Set as Input/Output Variable") are visible
through the automation layer -- this mirrors what the API itself exposes.

The COM server is a singleton per Windows session: repeated Dispatch() calls
attach to the *same* MathcadPrime.exe process, including one the user already
has open with their own visible documents. We therefore only ever terminate
the process in mathcad_quit() if we can prove *we* were the ones who spawned it
(no MathcadPrime.exe was running before our first Dispatch call).
"""

from __future__ import annotations

import os
from typing import Any

import psutil
import pythoncom
import win32com.client
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("mathcad-prime")

_app: Any = None
_worksheets: dict[str, Any] = {}
_owned_pid: int | None = None
_probed_ownership = False

DEFAULT_TIMEOUT_MS = 30000


def _ensure_com() -> None:
    try:
        pythoncom.CoInitialize()
    except pythoncom.com_error:
        pass


def _running_mathcad_pids() -> set[int]:
    return {p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == "MathcadPrime.exe"}


def _reset_gen_cache() -> None:
    import shutil

    import win32com
    from win32com.client import gencache

    root = win32com.__gen_path__
    for name in os.listdir(root) if os.path.isdir(root) else []:
        path = os.path.join(root, name)
        if os.path.isdir(path) and not os.path.exists(os.path.join(path, "__init__.py")):
            shutil.rmtree(path, ignore_errors=True)
    try:
        os.remove(os.path.join(root, "dicts.dat"))
    except OSError:
        pass
    gencache.Rebuild()


def _get_app() -> Any:
    global _app, _owned_pid, _probed_ownership
    _ensure_com()
    if _app is not None:
        return _app

    pids_before = _running_mathcad_pids()
    # Late binding cannot resolve Worksheets/Visible on this type library, so
    # generate the early-bound wrapper first and only fall back if that fails.
    try:
        app = win32com.client.gencache.EnsureDispatch("MathcadPrime.Application")
    except Exception:
        # Кэш типов pywin32 по умолчанию лежит во временной папке. Если её
        # почистили, от сгенерированного модуля остаётся пустой каталог и любой
        # Dispatch падает с AttributeError. Сносим кэш и генерируем заново.
        _reset_gen_cache()
        try:
            app = win32com.client.gencache.EnsureDispatch("MathcadPrime.Application")
        except Exception:
            app = win32com.client.Dispatch("MathcadPrime.Application")
    pids_after_probe = _running_mathcad_pids()
    if not (pids_after_probe - pids_before):
        # Prime was already running with the user's own windows: leave it alone.
        pass
    else:
        try:
            app.Visible = False
        except AttributeError:
            pass
    pids_after = _running_mathcad_pids()

    new_pids = pids_after - pids_before
    if len(new_pids) == 1:
        _owned_pid = next(iter(new_pids))
    _probed_ownership = True

    _app = app
    return _app


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _get_worksheet(path: str) -> Any:
    key = _norm(path)
    if key in _worksheets:
        return _worksheets[key]
    app = _get_app()
    ws = app.Open(path)
    _worksheets[key] = ws
    return ws


def _worksheet_info(ws: Any) -> dict:
    inputs = ws.Inputs
    outputs = ws.Outputs
    return {
        "name": ws.Name,
        "full_name": ws.FullName,
        "is_read_only": bool(ws.IsReadOnly),
        "modified": bool(ws.Modified),
        "inputs": [inputs.GetAliasByIndex(i) for i in range(inputs.Count)],
        "outputs": [outputs.GetAliasByIndex(i) for i in range(outputs.Count)],
    }


def _matrix_to_list(mx: Any) -> list[list[float]]:
    if mx is None:
        return []
    rows = int(mx.Rows)
    cols = int(mx.Columns)
    data = mx.Matrix
    return [[float(data[r][c]) for c in range(cols)] for r in range(rows)]


def _build_matrix(app: Any, ws: Any, values: list[list[float]]) -> Any:
    rows = len(values)
    cols = len(values[0]) if rows else 0
    mx = ws.CreateMatrix(rows, cols)
    for r in range(rows):
        for c in range(cols):
            mx.SetMatrixElement(r, c, float(values[r][c]))
    return mx


@mcp.tool()
def mathcad_open(path: str) -> dict:
    """Open (or attach to an already-open) Mathcad Prime worksheet (.mcdx/.mct/.mctx).

    Returns the worksheet name, whether it is read-only/modified, and the
    aliases of every variable currently tagged as an Input or Output variable
    in that worksheet -- those are the only names the other tools can act on.
    """
    ws = _get_worksheet(path)
    return _worksheet_info(ws)


@mcp.tool()
def mathcad_list_worksheets() -> list[dict]:
    """List every worksheet currently open in the running Mathcad Prime instance
    (including ones opened by the user through the GUI, not just via mathcad_open)."""
    app = _get_app()
    wss = app.Worksheets
    result = []
    for i in range(wss.Count):
        ws = wss.Item(i)
        result.append({"name": ws.Name, "full_name": ws.FullName})
    return result


@mcp.tool()
def mathcad_get_variables(path: str) -> dict:
    """Refresh and return the list of Input/Output variable aliases for a worksheet."""
    ws = _get_worksheet(path)
    inputs = ws.Inputs
    outputs = ws.Outputs
    return {
        "inputs": [inputs.GetAliasByIndex(i) for i in range(inputs.Count)],
        "outputs": [outputs.GetAliasByIndex(i) for i in range(outputs.Count)],
    }


@mcp.tool()
def mathcad_set_value(path: str, name: str, value: float, units: str = "") -> dict:
    """Set a scalar real (numeric) Input variable and trigger recalculation.

    units is a Mathcad unit string (e.g. "mm", "kg", "N*m"); pass "" for a
    dimensionless value. Returns {"error_code": 0} on success.
    """
    ws = _get_worksheet(path)
    code = ws.SetRealValue(name, float(value), units)
    return {"error_code": int(code)}


@mcp.tool()
def mathcad_set_string(path: str, name: str, value: str) -> dict:
    """Set a string Input variable and trigger recalculation."""
    ws = _get_worksheet(path)
    code = ws.SetStringValue(name, value)
    return {"error_code": int(code)}


@mcp.tool()
def mathcad_set_batch(path: str, values: list[dict], timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    """Set several Input variables atomically, then recalculate once.

    values: list of {"name": str, "value": float|str, "units": str (optional),
    "kind": "real"|"string" (optional, default "real")}.
    Returns per-item error codes keyed by name, plus the overall count.
    """
    ws = _get_worksheet(path)
    setter = ws.CreateValuesSetter()
    for item in values:
        kind = item.get("kind", "real")
        name = item["name"]
        if kind == "string":
            setter.AddStringValue(name, str(item["value"]))
        else:
            setter.AddScalarValue(name, float(item["value"]), item.get("units", ""))
    results = setter.SetValues(timeout_ms)
    per_item = {}
    for item in values:
        name = item["name"]
        try:
            per_item[name] = int(results.GetResultByAlias(name))
        except Exception as exc:  # noqa: BLE001
            per_item[name] = f"error: {exc}"
    return {"count": int(results.Count), "results": per_item}


@mcp.tool()
def mathcad_get_value(path: str, name: str, source: str = "output") -> dict:
    """Read a variable's current value (real, string, or matrix -- whichever applies).

    source: "output" (default) or "input". Returns error_code, units, and
    whichever of real_result / string_result / matrix is populated.
    """
    ws = _get_worksheet(path)
    getter = ws.OutputGetValue if source == "output" else ws.InputGetValue
    res = getter(name)
    matrix = None
    try:
        mres = res.MatrixResult
        if mres is not None and int(mres.Rows) > 0:
            matrix = _matrix_to_list(mres)
    except Exception:  # noqa: BLE001
        matrix = None
    return {
        "error_code": int(res.ErrorCode),
        "units": res.Units,
        "real_result": float(res.RealResult),
        "string_result": res.StringResult,
        "matrix": matrix,
    }


@mcp.tool()
def mathcad_get_value_as(path: str, name: str, units: str) -> dict:
    """Read a scalar Output variable converted into the given target units."""
    ws = _get_worksheet(path)
    res = ws.OutputGetRealValueAs(name, units)
    return {"error_code": int(res.ErrorCode), "real_result": float(res.RealResult)}


@mcp.tool()
def mathcad_get_matrix(path: str, name: str, source: str = "output", units: str | None = None) -> dict:
    """Read a matrix Input/Output variable. If units is given (output only),
    the matrix is converted into those units first."""
    ws = _get_worksheet(path)
    if source == "output" and units:
        res = ws.OutputGetMatrixValueAs(name, units)
        return {"error_code": int(res.ErrorCode), "matrix": _matrix_to_list(res.MatrixResult), "units": units}
    getter = ws.OutputGetMatrixValue if source == "output" else ws.InputGetMatrixValue
    res = getter(name)
    return {"error_code": int(res.ErrorCode), "matrix": _matrix_to_list(res.MatrixResult), "units": res.Units}


@mcp.tool()
def mathcad_set_matrix(path: str, name: str, values: list[list[float]], units: str = "") -> dict:
    """Set a matrix Input variable (list of rows of numbers) and recalculate."""
    app = _get_app()
    ws = _get_worksheet(path)
    mx = _build_matrix(app, ws, values)
    code = ws.SetMatrixValue(name, mx, units)
    return {"error_code": int(code)}


@mcp.tool()
def mathcad_recalculate(path: str) -> dict:
    """Force the worksheet to fully recalculate (Synchronize)."""
    ws = _get_worksheet(path)
    ws.Synchronize()
    return {"modified": bool(ws.Modified)}


@mcp.tool()
def mathcad_save(path: str) -> dict:
    """Save the worksheet in place."""
    ws = _get_worksheet(path)
    ws.Save()
    return {"saved": True, "full_name": ws.FullName}


@mcp.tool()
def mathcad_save_as(path: str, new_path: str) -> dict:
    """Save the worksheet to a new path; subsequent tool calls can use either path."""
    ws = _get_worksheet(path)
    ws.SaveAs(new_path)
    _worksheets[_norm(new_path)] = ws
    return {"saved": True, "full_name": ws.FullName}


@mcp.tool()
def mathcad_quit() -> dict:
    """Shut down the Mathcad Prime instance -- but ONLY if this server itself
    started it. If it attached to a Mathcad Prime the user already had open
    (same-session COM singleton), this is a safe no-op that leaves that
    process and the user's own documents untouched."""
    global _app, _owned_pid, _worksheets
    if _owned_pid is None:
        _app = None
        _worksheets = {}
        msg = (
            "Не запускал отдельный процесс Mathcad Prime (подключился к уже "
            "открытому) - ничего не закрываю."
            if _probed_ownership
            else "Mathcad Prime ещё не запускался в этой сессии."
        )
        return {"terminated": False, "reason": msg}

    try:
        proc = psutil.Process(_owned_pid)
        proc.terminate()
        proc.wait(timeout=10)
    except psutil.NoSuchProcess:
        pass
    _app = None
    _worksheets = {}
    pid = _owned_pid
    _owned_pid = None
    return {"terminated": True, "pid": pid}


from . import tools_ws  # noqa: E402  (needs `mcp` and `_get_app` defined above)

tools_ws.register(mcp, _get_app)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
