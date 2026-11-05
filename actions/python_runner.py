"""Optional advanced Python actions.

Safety model (defense in depth, not a perfect sandbox):
  * disabled unless ENABLE_PYTHON_ACTIONS=true
  * source is statically checked (allow-listed imports, no dunder access, no eval/exec/open/...)
  * only runs after explicit approval, and re-approval is required after every edit
  * executes in a separate `python -I -S` subprocess with empty environment, temp cwd,
    CPU/memory/file limits (POSIX), a wall-clock timeout and capped output
  * NEVER executes inside the Flask web process
For production use, additionally run the app in a container without network access.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .python_worker import ALLOWED_MODULES

WORKER = Path(__file__).with_name("python_worker.py")
BANNED_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "input", "breakpoint", "memoryview", "type", "object", "super", "classmethod", "staticmethod",
    "property", "help", "dir", "exit", "quit",
}
MAX_CODE = 5000
MAX_OUTPUT = 20_000


class CodeRejected(ValueError):
    pass


def validate_code(code: str) -> None:
    if not isinstance(code, str) or not code.strip():
        raise CodeRejected("Python code is empty.")
    if len(code) > MAX_CODE:
        raise CodeRejected(f"Python code must be at most {MAX_CODE} characters.")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeRejected(f"Syntax error on line {exc.lineno}: {exc.msg}") from exc
    has_run = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run" and node in tree.body:
            has_run = True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if isinstance(node, ast.ImportFrom) and node.level:
                raise CodeRejected("Relative imports are not allowed.")
            for n in names:
                if n.split(".")[0] not in ALLOWED_MODULES:
                    raise CodeRejected(f"Importing '{n}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_MODULES))}.")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise CodeRejected("Accessing attributes that start with an underscore is not allowed.")
        elif isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            raise CodeRejected(f"'{node.id}' is not allowed.")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith, ast.ClassDef)):
            raise CodeRejected("Classes and async code are not allowed.")
    if not has_run:
        raise CodeRejected("Define a top-level function: def run(args): ... that returns a dictionary.")


def run_code(code: str, args: dict, timeout: float = 5) -> dict:
    """Run validated code in a subprocess and return the JSON result dict."""
    validate_code(code)
    with tempfile.TemporaryDirectory(prefix="pyaction_") as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", str(WORKER)],
                input=json.dumps({"code": code, "args": args}),
                capture_output=True, text=True, timeout=timeout, cwd=tmp, env={},
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"The Python action took longer than {timeout:g} seconds and was stopped.") from exc
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError("The Python action crashed or exceeded its resource limits.")
    out = proc.stdout[:MAX_OUTPUT]
    try:
        payload = json.loads(out)
    except ValueError as exc:
        raise RuntimeError("The Python action returned invalid output.") from exc
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "The Python action failed."))
    result = payload["result"]
    if not isinstance(result, dict):
        raise RuntimeError("The Python action must return a dictionary.")
    return result
