"""Limit automatic corrections to ordinary calculations and read-only analysis.

This is a conservative retry policy, not a Python sandbox. Unknown calls require
the user to review and run the cell in the notebook.
"""

import ast

from core.workspace.errors import ErrorCode, WorkspaceError

REPEATABLE_FUNCTIONS = frozenset({
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list",
    "max", "min", "print", "range", "round", "set", "sorted", "str", "sum", "tuple", "zip",
})
REPEATABLE_METHODS = frozenset({
    "astype", "copy", "corr", "count", "describe", "dropna", "head", "hist", "info",
    "isna", "max", "mean", "median", "min", "plot", "read_csv", "read_json",
    "read_parquet", "shape", "sort_values", "std", "sum", "tail", "value_counts",
})
REPEATABLE_NODES = frozenset({
    ast.Module, ast.Expr, ast.Assign, ast.AnnAssign, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Subscript, ast.Slice, ast.Attribute,
    ast.Call, ast.keyword,
})
UNSAFE_RETRY_KEYWORDS = frozenset({
    "backend", "converters", "default", "file", "inplace", "key", "out", "storage_options",
})


def is_repeatable_call(call: ast.Call) -> bool:
    if any(keyword.arg is None or keyword.arg in UNSAFE_RETRY_KEYWORDS for keyword in call.keywords):
        return False
    if isinstance(call.func, ast.Name):
        return call.func.id in REPEATABLE_FUNCTIONS
    if isinstance(call.func, ast.Attribute):
        return call.func.attr in REPEATABLE_METHODS
    return False


def is_repeatable_node(node: ast.AST) -> bool:
    is_operator = isinstance(node, (ast.operator, ast.unaryop, ast.boolop, ast.cmpop))
    return type(node) in REPEATABLE_NODES or is_operator


def is_repeatable_source(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError):
        return False
    for node in ast.walk(tree):
        if not is_repeatable_node(node):
            return False
        if isinstance(node, ast.Call) and not is_repeatable_call(node):
            return False
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return False
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store):
            return False
    return True


def require_repeatable_correction(previous_source: str, replacement_source: str) -> None:
    if not all(is_repeatable_source(source) for source in (previous_source, replacement_source)):
        raise WorkspaceError(
            ErrorCode.BAD_PAYLOAD,
            "This step may have persistent or unknown effects. Ask the user to review and run it in the notebook; "
            "it was not edited or rerun.",
        )
