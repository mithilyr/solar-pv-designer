"""A small, safe arithmetic evaluator for editable BOM rules.

Rules are plain formulas such as ``2 * (panels - runs)`` or
``ceil(rail_bases * 0.2)``. Only numbers, the named quantities, arithmetic
operators, comparisons, ``x if cond else y`` and a few math functions are
allowed, so a rules file can never run code.
"""

from __future__ import annotations

import ast
import math
import operator

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CMPOPS = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
FUNCTIONS = {
    "ceil": math.ceil, "floor": math.floor, "round": round, "min": min, "max": max, "abs": abs,
}


class FormulaError(ValueError):
    pass


def evaluate(expr: str, names: dict[str, float]) -> float:
    expr = (expr or "").strip()
    if not expr:
        raise FormulaError("Empty formula")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Cannot read formula: {expr}") from exc
    return _eval(tree.body, names)


def _eval(node, names):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise FormulaError(f"Unknown name '{node.id}'")
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, names), _eval(node.right, names))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand, names)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMPOPS:
        return _CMPOPS[type(node.ops[0])](_eval(node.left, names), _eval(node.comparators[0], names))
    if isinstance(node, ast.IfExp):
        return _eval(node.body, names) if _eval(node.test, names) else _eval(node.orelse, names)
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, names) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in FUNCTIONS and not node.keywords):
        return FUNCTIONS[node.func.id](*[_eval(a, names) for a in node.args])
    raise FormulaError(f"Not allowed in a formula: {ast.unparse(node)}")


def names_used(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in FUNCTIONS}
