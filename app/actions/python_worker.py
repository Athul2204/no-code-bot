"""Worker executed in a SEPARATE interpreter process (never inside Flask).

Reads {"code": str, "args": dict} as JSON on stdin, runs the user's `run(args)` function with a
restricted set of builtins and importable modules, and prints {"ok": bool, "result"|"error": ...}.
Resource limits are applied where the OS supports them (POSIX).
"""
import builtins
import json
import sys

ALLOWED_MODULES = {"math", "json", "re", "datetime", "statistics", "decimal", "fractions", "string", "random", "itertools", "functools", "collections", "textwrap"}
SAFE_BUILTINS = [
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset", "int",
    "isinstance", "len", "list", "map", "max", "min", "pow", "print", "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "str", "sum", "tuple", "zip", "ValueError", "TypeError", "KeyError", "IndexError",
    "Exception", "ZeroDivisionError", "True", "False", "None",
]


def _limits():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    except Exception:  # not available (Windows) - the parent still enforces a wall-clock timeout
        pass


def main():
    payload = json.loads(sys.stdin.read())
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name.split(".")[0] not in ALLOWED_MODULES:
            raise ImportError(f"Importing '{name}' is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    safe = {n: getattr(builtins, n) for n in SAFE_BUILTINS if hasattr(builtins, n)}
    safe["__import__"] = guarded_import
    namespace = {"__builtins__": safe, "__name__": "action"}
    _limits()
    try:
        exec(compile(payload["code"], "<action>", "exec"), namespace)  # noqa: S102 - sandboxed worker process
        result = namespace["run"](payload["args"])
        out = {"ok": True, "result": result}
        json.dumps(out)  # must be JSON serialisable
    except BaseException as exc:  # noqa: BLE001
        out = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:500]}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
