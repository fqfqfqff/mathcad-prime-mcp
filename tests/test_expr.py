"""Тесты разбора формул и сборки листа. Mathcad для них не нужен."""

import xml.etree.ElementTree as ET

import pytest

from mathcad_prime_mcp.ws import doc, textblock
from mathcad_prime_mcp.ws.expr import (define_xml, eval_xml, from_xml, parse_expr,
                                       to_xml)

NS = {"ml": "http://schemas.mathsoft.com/math50"}


def roundtrip(src: str) -> str:
    """текст -> XML -> текст: смысл не должен теряться."""
    xml = to_xml(parse_expr(src))
    return from_xml(ET.fromstring('<root xmlns:ml="%s">%s</root>' % (NS["ml"], xml))[0])


@pytest.mark.parametrize("src", [
    "5",
    "0.125",
    "a+b",
    "a-b*c",
    "a*b+c",
    "(a+b)*c",
    "2^ceil(log(L,2))",
    "m/2",
    "rnd(m)-m/2",
    "S[n]+D[n]",
    "|C[j]|",
    "C[j]*Phi(M[j]-h)",
    "A*sin(2*pi*f*t+Q)",
    "0..L-1",
    "READWAV(\"lab4.wav\")",
    "WRITEWAV(\"o.wav\",44100,16,V)",
])
def test_roundtrip(src):
    assert roundtrip(src) == src


def test_aliases_become_greek():
    xml = to_xml(parse_expr("Phi(x)"))
    assert "Φ" in xml
    assert roundtrip("Phi(x)") == "Phi(x)"


def test_precedence_needs_no_extra_parens():
    # a+b*c это a+(b*c), лишних скобок в тексте быть не должно
    assert roundtrip("a+b*c") == "a+b*c"


def test_define_forms():
    assert "<ml:define>" in define_xml(parse_expr("x"), parse_expr("1"))
    idx = define_xml(parse_expr("U[i]"), parse_expr("1"))
    assert 'labels="*"' in idx          # индекс слева помечается именно так
    fn = define_xml(parse_expr("M1(t)"), parse_expr("A"))
    assert "<ml:function>" in fn and "<ml:boundVars>" in fn


def test_eval_has_unit_override():
    assert "<ml:unitOverride>" in eval_xml(parse_expr("L"))


def test_bad_syntax_is_reported():
    with pytest.raises(SyntaxError):
        parse_expr("a +")
    with pytest.raises(SyntaxError):
        doc.math_region("просто текст без присваивания")


SRC = """\
@text Раздел один
a := 5 ; b := a*2 ; b=
@note a - что-то важное
@plot x=n y=S[n]:red:2,V[n]:navy:1 xlim=0..200
"""


def test_build_worksheet_structure():
    xml, man, extra = doc.build_worksheet(SRC)
    kinds = [m["kind"] for m in man]
    assert kinds == ["text", "math", "math", "math", "note", "plot"]
    assert xml.startswith("<worksheet") and xml.endswith("</worksheet>")
    assert len(extra["parts"]) == 1 and len(extra["rels"]) == 1
    # регион текста ссылается на ту же связь, что объявлена в rels
    assert extra["rels"][0][0] in xml


def test_rows_do_not_overlap_and_fit_the_page():
    xml, man, _ = doc.build_worksheet(
        "aaa := 1 ; bbb := 2 ; ccc := 3\nlonger_name := 4 ; x=\n")
    rows = {}
    for m in man:
        if m["kind"] == "math":
            rows.setdefault(m["top"], []).append((m["left"], m["left"] + m["width"]))
    for cells in rows.values():
        cells.sort()
        for (_, end), (start, _) in zip(cells, cells[1:]):
            assert end <= start, "регионы наезжают друг на друга"
        assert cells[-1][1] <= doc.PRINT_W + doc.LEFT0 + 1


def test_tall_rows_get_room_above():
    """Дробь рисуется выше базовой линии, значит ряду нужен запас сверху."""
    _, man, _ = doc.build_worksheet("a := 1\nb := m/2\n")
    tops = [m["top"] for m in man]
    assert tops[1] - tops[0] > doc.ROW_GAP


def test_plot_never_straddles_a_page_break():
    src = "a := 1\n" + "@gap 900\n" + "@plot x=n y=a\n"
    _, man, _ = doc.build_worksheet(src)
    plot = [m for m in man if m["kind"] == "plot"][0]
    page = int(plot["top"] // doc.PAGE_H)
    assert plot["top"] + plot["height"] <= (page + 1) * doc.PAGE_H


def test_textblock_package_is_a_zip_with_the_text():
    import io
    import zipfile
    data = textblock.xaml_package("Проверка")
    z = zipfile.ZipFile(io.BytesIO(data))
    assert "Xaml/Document.xaml" in z.namelist()
    assert "Проверка" in z.read("Xaml/Document.xaml").decode("utf-8")


@pytest.mark.parametrize("src", [
    "sum(i, 0..10, i)",
    "sum(k, 0..N-1, x[k]*y[n-k])",
    "prod(i, 1..5, i)",
])
def test_iterated_operators_roundtrip(src):
    assert roundtrip(src) == src


def test_summation_xml_has_both_bounds():
    xml = to_xml(parse_expr("sum(i, 0..N, i)"))
    for tag in ("<ml:summation", "<ml:lambda>", "<ml:lowerBound>", "<ml:upperBound>"):
        assert tag in xml


def test_error_message_is_extracted_despite_namespace():
    """Раньше текст ошибки терялся: дети engineError лежат в своём namespace."""
    from mathcad_prime_mcp.ws.results import _parse_results
    xml = (
        '<resultsList xmlns="http://schemas.mathsoft.com/result10">'
        '<resultData result-id="0" calculation-status="Synchronized"><engineErrors>'
        '<engineError error-id="0"><errorCode>bad%_variable\tdoc_mc_i\t</errorCode>'
        '<resource-string>Эта переменная не определена.</resource-string>'
        "</engineError></engineErrors></resultData></resultsList>")
    err = _parse_results(xml)[0]["errors"][0]
    assert err["text"] == "Эта переменная не определена."
    assert err["code"] == "bad%_variable"
