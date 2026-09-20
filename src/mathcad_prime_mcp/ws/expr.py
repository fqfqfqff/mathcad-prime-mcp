"""Bidirectional bridge between a compact text notation and Mathcad Prime's
math50 XML.

Text -> XML lets a worksheet be authored from a few lines of near-Mathcad
source; XML -> text lets an existing .mcdx be read back without screenshots.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

ML = "http://schemas.mathsoft.com/math50"
SP = ' xml:space="preserve"'

# ASCII aliases for symbols that are painful to type / paste
ALIASES = {
    "Phi": "Φ",   # Heaviside step, the "оператор фильтрации" of the labs
    "phi": "φ",
    "sigma": "σ",
    "pi": "π",
    "Delta": "Δ",
    "omega": "ω",
    "tau": "τ",
}
UNALIAS = {v: k for k, v in ALIASES.items()}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------
@dataclass
class Node:
    kind: str
    value: Any = None
    args: list["Node"] = field(default_factory=list)


def Num(v):            return Node("num", v)
def Str(v):            return Node("str", v)
def Id(v):             return Node("id", v)
def Call(f, a):        return Node("call", f, a)
def Index(b, i):       return Node("index", None, [b, i])
def Bin(op, l, r):     return Node("bin", op, [l, r])
def Neg(x):            return Node("neg", None, [x])
def Absv(x):           return Node("abs", None, [x])
def Rng(a, b):         return Node("range", None, [a, b])
def Paren(x):          return Node("paren", None, [x])
def Placeholder():     return Node("placeholder")


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------
TOKEN_RE = re.compile(
    r"""\s*(?:
      (?P<num>\d+\.\d+|\.\d+|\d+)
    | (?P<str>"[^"]*")
    | (?P<range>\.\.)
    | (?P<assign>:=)
    | (?P<name>[A-Za-z_Α-ωА-я][A-Za-z_0-9Α-ωА-я]*)
    | (?P<op>[-+*/^(),\[\]|=])
    )""",
    re.X,
)


def tokenize(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            if src[pos:].strip() == "":
                break
            raise SyntaxError(f"не разобрать: {src[pos:]!r}")
        pos = m.end()
        kind = m.lastgroup
        text = m.group(kind)
        out.append((kind, text))
    return out


# --------------------------------------------------------------------------
# Parser (precedence climbing)
# --------------------------------------------------------------------------
BIN_PREC = {"..": 5, "+": 10, "-": 10, "*": 20, "/": 20, "^": 40}
RIGHT_ASSOC = {"^"}


class Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, text):
        kind, got = self.next()
        if got != text:
            raise SyntaxError(f"ожидалось {text!r}, получено {got!r}")

    def parse(self, min_prec=0):
        left = self.unary()
        while True:
            kind, text = self.peek()
            if kind == "range":
                text = ".."
            prec = BIN_PREC.get(text if kind in ("op", "range") else None, -1)
            if prec < 0 or prec < min_prec:
                break
            self.next()
            nxt = prec if text in RIGHT_ASSOC else prec + 1
            right = self.parse(nxt)
            left = Rng(left, right) if text == ".." else Bin(text, left, right)
        return left

    def unary(self):
        kind, text = self.peek()
        if text == "-":
            self.next()
            return Neg(self.unary())
        return self.postfix()

    def postfix(self):
        node = self.atom()
        while True:
            kind, text = self.peek()
            if text == "[":
                self.next()
                idx = self.parse()
                self.expect("]")
                node = Index(node, idx)
            else:
                break
        return node

    def atom(self):
        kind, text = self.next()
        if kind == "num":
            return Num(text)
        if kind == "str":
            return Str(text[1:-1])
        if text == "(":
            inner = self.parse()
            self.expect(")")
            return Paren(inner)
        if text == "|":
            inner = self.parse()
            self.expect("|")
            return Absv(inner)
        if kind == "name":
            name = ALIASES.get(text, text)
            if self.peek()[1] == "(":
                self.next()
                args = []
                if self.peek()[1] != ")":
                    args.append(self.parse())
                    while self.peek()[1] == ",":
                        self.next()
                        args.append(self.parse())
                self.expect(")")
                return Call(name, args)
            return Id(name)
        raise SyntaxError(f"неожиданный токен {text!r}")


def parse_expr(src: str) -> Node:
    p = Parser(tokenize(src))
    node = p.parse()
    if p.i != len(p.t):
        raise SyntaxError(f"лишнее в конце: {src!r}")
    return node


# --------------------------------------------------------------------------
# AST -> XML
# --------------------------------------------------------------------------
OP_TAG = {"+": "plus", "-": "minus", "*": "mult", "/": "div", "^": "pow"}
# Σ и Π: пишутся как sum(i, 0..N, тело) и prod(i, 1..N, тело)
ITERATED = {"sum": "summation", "prod": "product"}


def _id_xml(name: str, decl: bool = False, star: bool = False) -> str:
    if star:
        return f'<ml:id labels="*"{SP}>{esc(name)}</ml:id>'
    contextual = "" if decl else ' label-is-contextual="true"'
    return f'<ml:id labels="VARIABLE"{contextual}{SP}>{esc(name)}</ml:id>'


def to_xml(n: Node) -> str:
    k = n.kind
    if k == "num":
        return f"<ml:real>{n.value}</ml:real>"
    if k == "str":
        return f"<ml:str{SP}>{esc(n.value)}</ml:str>"
    if k == "id":
        return _id_xml(n.value)
    if k == "placeholder":
        return "<ml:placeholder />"
    if k == "call" and n.value in ITERATED and len(n.args) == 3 and n.args[1].kind == "range":
        # sum(i, 0..N, тело) -> оператор Σ с переменной, пределами и телом
        var, rng, body = n.args
        if var.kind != "id":
            raise ValueError("первый аргумент %s это имя переменной" % n.value)
        return ('<ml:apply><ml:%s /><ml:lambda><ml:boundVars>%s</ml:boundVars>%s</ml:lambda>'
                '<ml:lowerBound>%s</ml:lowerBound><ml:upperBound>%s</ml:upperBound></ml:apply>'
                % (ITERATED[n.value], _id_xml(var.value, decl=True), to_xml(body),
                   to_xml(rng.args[0]), to_xml(rng.args[1])))
    if k == "call":
        inner = "".join(to_xml(a) for a in n.args)
        if len(n.args) != 1:
            inner = f"<ml:sequence>{inner}</ml:sequence>"
        fn = f'<ml:id labels="FUNCTION" label-is-contextual="true"{SP}>{esc(n.value)}</ml:id>'
        return f"<ml:apply>{fn}{inner}</ml:apply>"
    if k == "index":
        return "<ml:apply><ml:indexer />%s%s</ml:apply>" % (to_xml(n.args[0]), to_xml(n.args[1]))
    if k == "bin":
        return "<ml:apply><ml:%s />%s%s</ml:apply>" % (
            OP_TAG[n.value], to_xml(n.args[0]), to_xml(n.args[1]))
    if k == "neg":
        return f"<ml:apply><ml:neg />{to_xml(n.args[0])}</ml:apply>"
    if k == "abs":
        return f"<ml:apply><ml:absval />{to_xml(n.args[0])}</ml:apply>"
    if k == "paren":
        return f"<ml:parens>{to_xml(n.args[0])}</ml:parens>"
    if k == "range":
        return f"<ml:range>{to_xml(n.args[0])}{to_xml(n.args[1])}</ml:range>"
    raise ValueError(f"нечего писать для узла {k}")


def lhs_xml(n: Node) -> str:
    """Left-hand side of ':=' -- plain variable, indexed element, or function."""
    if n.kind == "id":
        return _id_xml(n.value, decl=True)
    if n.kind == "index":
        base, idx = n.args
        if base.kind != "id" or idx.kind != "id":
            raise ValueError("слева от := индекс должен быть вида X[i]")
        return "<ml:apply><ml:indexer />%s%s</ml:apply>" % (
            _id_xml(base.value, decl=True), _id_xml(idx.value, star=True))
    if n.kind == "call":
        bound = "".join(_id_xml(a.value, decl=True) for a in n.args)
        return "<ml:function>%s<ml:boundVars>%s</ml:boundVars></ml:function>" % (
            _id_xml(n.value, decl=True), bound)
    raise ValueError("слева от := ожидается имя, X[i] или f(x)")


def define_xml(lhs: Node, rhs: Node) -> str:
    return f"<ml:define>{lhs_xml(lhs)}{to_xml(rhs)}</ml:define>"


def eval_xml(expr: Node) -> str:
    return ("<ml:eval>%s<ml:unitOverride><ml:placeholder /></ml:unitOverride></ml:eval>"
            % to_xml(expr))


# --------------------------------------------------------------------------
# XML -> text  (reading an existing worksheet back)
# --------------------------------------------------------------------------
_TAG_OP = {"plus": "+", "minus": "-", "mult": "*", "div": "/", "pow": "^"}
_PREC = {"+": 10, "-": 10, "*": 20, "/": 20, "^": 40}


def _lname(el) -> str:
    return el.tag.split("}")[-1]


def _unalias(name: str) -> str:
    return UNALIAS.get(name, name)


def from_xml(el) -> str:
    """Render a math50 element back to the compact text notation."""
    t = _lname(el)
    if t == "real":
        return (el.text or "").strip()
    if t == "str":
        return '"%s"' % (el.text or "")
    if t == "id":
        return _unalias((el.text or "").strip())
    if t == "placeholder":
        return "?"
    if t == "parens":
        return "(%s)" % from_xml(list(el)[0])
    if t == "range":
        a, b = list(el)
        return "%s..%s" % (from_xml(a), from_xml(b))
    if t == "sequence":
        return ",".join(from_xml(c) for c in el)
    if t == "function":
        kids = list(el)
        name = from_xml(kids[0])
        bound = ",".join(from_xml(c) for c in kids[1]) if len(kids) > 1 else ""
        return f"{name}({bound})"
    if t == "define":
        kids = list(el)
        return "%s := %s" % (from_xml(kids[0]), from_xml(kids[1]))
    if t == "eval":
        kids = list(el)
        return "%s =" % from_xml(kids[0])
    if t == "apply":
        kids = list(el)
        head = _lname(kids[0])
        if head == "indexer":
            return "%s[%s]" % (from_xml(kids[1]), from_xml(kids[2]))
        if head == "absval":
            return "|%s|" % from_xml(kids[1])
        if head == "scale":
            return "%s*%s" % (from_xml(kids[1]), from_xml(kids[2]))
        if head == "neg":
            return "-%s" % from_xml(kids[1])
        if head in ("summation", "product"):
            lam = kids[1]
            var = from_xml(list(lam[0])[0])
            body = from_xml(list(lam)[1])
            lo = from_xml(list(kids[2])[0])
            hi = from_xml(list(kids[3])[0])
            name = "sum" if head == "summation" else "prod"
            return "%s(%s, %s..%s, %s)" % (name, var, lo, hi, body)
        if head in _TAG_OP:
            op = _TAG_OP[head]
            return "%s%s%s" % (from_xml(kids[1]), op, from_xml(kids[2]))
        # function call: first child is the function id
        fn = from_xml(kids[0])
        rest = kids[1:]
        if len(rest) == 1 and _lname(rest[0]) == "sequence":
            return "%s(%s)" % (fn, from_xml(rest[0]))
        return "%s(%s)" % (fn, ",".join(from_xml(c) for c in rest))
    # unknown: fall back to the tag name so nothing is silently dropped
    return f"<{t}>"


def parse_math_element(xml_text: str):
    return ET.fromstring(xml_text)
