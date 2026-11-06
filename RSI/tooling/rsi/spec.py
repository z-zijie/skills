"""Operator specs: shapes, tolerances, and the byte and flop formulas behind the roofline.

A spec is read-only to everyone but the human. It carries the two things an agent must not be
able to move: the tolerances that decide correctness and the cost model that decides what the
theoretical bound is.
"""

from __future__ import annotations

import ast
import math
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import jsonschema_lite, paths, yamlite

DTYPE_BYTES = {
    "fp64": 8, "fp32": 4, "fp16": 2, "bf16": 2,
    "int64": 8, "int32": 4, "int16": 2, "int8": 1, "uint8": 1,
}

#: Arithmetic names a cost-model expression may use.
_ALLOWED_NAMES = {"numel", "size_of", "ceil", "floor", "sqrt", "log2", "min", "max", "shape"}
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
    ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow, ast.Mod,
    ast.USub, ast.UAdd, ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.IfExp,
    ast.BoolOp, ast.And, ast.Or, ast.Not, ast.Subscript, ast.Tuple,
)


class SpecError(ValueError):
    """Raised when a spec is malformed or a cost-model expression is unsafe."""


class Spec:
    """One ``operators/<op>/spec.yaml``."""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self.data = data
        self.path = path
        self.op = data["op"]
        self.dtypes: List[str] = list(data["dtype"])
        self.tolerance = data["tolerance"]
        self.shapes = data["shapes"]
        self.cost_model = data["cost_model"]
        self.entry = data["entry"]
        self.setup_cap_us = data.get("setup_cap_us")
        self.setup_cap_mode = data.get("setup_cap_mode", "relative")
        self.speedup_review_cap = data.get("speedup_review_cap", 2.0)
        self._bytes_fn = _compile_expr(self.cost_model["bytes_expr"], "bytes_expr")
        self._flops_fn = _compile_expr(self.cost_model["flops_expr"], "flops_expr")

    # -- construction -----------------------------------------------------
    @classmethod
    def load(cls, op_or_path: Any, root: Optional[Path] = None) -> "Spec":
        if isinstance(op_or_path, (str, Path)) and str(op_or_path).endswith((".yaml", ".yml")):
            path = Path(op_or_path)
        else:
            path = paths.op_dir(str(op_or_path), root) / "spec.yaml"
        if not path.is_file():
            raise SpecError(f"no spec at {path}")
        schema = jsonschema_lite.load_schema(str(paths.repo_root() / "schemas" / "operator-spec.schema.json"))
        data = yamlite.load(path)
        if not isinstance(data, dict):
            raise SpecError(f"{path}: a spec must be a mapping")
        jsonschema_lite.check(data, schema, label=str(path))
        return cls(data, path)

    @property
    def directory(self) -> Path:
        if self.path is None:
            raise SpecError("spec was constructed without a path")
        return self.path.parent

    # -- shapes -----------------------------------------------------------
    def cases(self, count: Optional[int] = None, seed: int = 0) -> List[Dict[str, int]]:
        """Typical, edge and fresh-random shapes.

        Random shapes are drawn on every run because a kernel specialised for fixed shapes
        must not be able to pass: specialisation shows up as a correctness or timing cliff on
        shapes nobody has seen.
        """
        rng = random.Random(seed)
        cases: List[Dict[str, int]] = []
        for shape in self.shapes.get("typical", []):
            cases.append(dict(shape))
        for shape in self.shapes.get("edge", []):
            cases.append(dict(shape))
        random_cfg = self.shapes.get("random", {}) or {}
        wanted = count if count is not None else int(random_cfg.get("count", 0))
        max_dim = int(random_cfg.get("max_dim", 4096))
        template = dict(self.shapes.get("typical", [{}])[0])
        for _ in range(wanted):
            shape = dict(template)
            for axis in shape:
                shape[axis] = rng.randint(1, max_dim)
            cases.append(shape)
        return cases

    def dtype_for(self, case: Dict[str, int]) -> str:
        return self.dtypes[0]

    # -- cost model -------------------------------------------------------
    def model(self, case: Dict[str, int], dtype: Optional[str] = None) -> Dict[str, float]:
        """Bytes moved across device memory and flops performed for one case."""
        dtype = dtype or self.dtype_for(case)
        if dtype not in DTYPE_BYTES:
            raise SpecError(f"{self.op}: {dtype!r} is not a supported dtype")
        env = _expression_env(case, dtype)
        return {
            "bytes": float(self._bytes_fn(env)),
            "flops": float(self._flops_fn(env)),
            "dtype_bytes": DTYPE_BYTES[dtype],
        }

    # -- correctness ------------------------------------------------------
    def compare(self, reference: Any, candidate: Any) -> Tuple[bool, Dict[str, float]]:
        """Apply the spec's tolerance to two flat float lists."""
        kind = self.tolerance["kind"]
        if len(reference) != len(candidate):
            return False, {"max_abs_err": math.inf, "max_rel_err": math.inf}
        max_abs = 0.0
        max_rel = 0.0
        if kind == "bit_exact":
            for left, right in zip(reference, candidate):
                if left != right:
                    return False, {"max_abs_err": abs(float(left) - float(right)), "max_rel_err": math.inf}
            return True, {"max_abs_err": 0.0, "max_rel_err": 0.0}
        rtol = float(self.tolerance.get("rtol", 0.0))
        atol = float(self.tolerance.get("atol", 0.0))
        for left, right in zip(reference, candidate):
            left_f, right_f = float(left), float(right)
            abs_err = abs(left_f - right_f)
            if math.isnan(right_f) or math.isinf(right_f):
                return False, {"max_abs_err": math.inf, "max_rel_err": math.inf}
            rel_err = abs_err / max(abs(left_f), 1e-30)
            max_abs = max(max_abs, abs_err)
            max_rel = max(max_rel, rel_err)
            if abs_err > atol + rtol * abs(left_f):
                return False, {"max_abs_err": max_abs, "max_rel_err": max_rel}
        return True, {"max_abs_err": max_abs, "max_rel_err": max_rel}


def _expression_env(case: Dict[str, int], dtype: str) -> Dict[str, Any]:
    def size_of(name: str) -> int:
        return DTYPE_BYTES[str(name)]

    def numel() -> int:
        total = 1
        for value in case.values():
            total *= int(value)
        return total

    def shape(axis: int) -> int:
        keys = list(case)
        return int(case[keys[axis]])

    return {
        "numel": numel(),
        "size_of": size_of,
        "ceil": math.ceil,
        "floor": math.floor,
        "sqrt": math.sqrt,
        "log2": math.log2,
        "min": min,
        "max": max,
        "shape": shape,
        "dtype": dtype,
        **{key: value for key, value in case.items()},
    }


def _compile_expr(expression: str, label: str) -> Callable[[Dict[str, Any]], float]:
    """Compile a cost-model formula after refusing anything but arithmetic."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise SpecError(f"{label}: cannot parse {expression!r}: {error}") from error
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise SpecError(f"{label}: {type(node).__name__} is not allowed in a cost formula")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise SpecError(f"{label}: unknown name {node.id!r} in a cost formula")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_NAMES:
                raise SpecError(f"{label}: only arithmetic helpers may be called")
    code = compile(tree, "<spec>", "eval")
    return lambda env: eval(code, {"__builtins__": {}}, env)  # noqa: S307 - audited above


def random_case(spec: Spec, seed: int, max_dim: int = 1024) -> Dict[str, int]:
    rng = random.Random(seed)
    template = dict(spec.shapes.get("typical", [{}])[0])
    return {axis: rng.randint(1, max_dim) for axis in template}
