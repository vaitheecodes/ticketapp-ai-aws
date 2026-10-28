"""Every third-party package the deployed backend imports must be in
requirements-runtime.txt (AppSail doesn't install anything else), and the
heavy pipeline libraries must stay out of it."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# import name -> distribution name, where they differ
DIST = {"jwt": "pyjwt", "dotenv": "python-dotenv", "typing_extensions": "typing-extensions"}
# Imported only lazily by optional providers; not needed with quickml_appsail/mock.
OPTIONAL = {"openai"}
# Pulled in transitively by fastapi/pydantic, and botocore by boto3.
TRANSITIVE = {"pydantic", "starlette", "typing-extensions", "botocore"}


def _runtime_requirements() -> set[str]:
    lines = (ROOT / "requirements-runtime.txt").read_text().splitlines()
    return {re.split(r"[<>=\[ ]", line.strip(), maxsplit=1)[0].lower()
            for line in lines if line.strip() and not line.startswith("#")}


def _backend_imports() -> set[str]:
    names = set()
    for path in (ROOT / "backend").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return {n for n in names if n not in sys.stdlib_module_names and n not in ("backend", "__future__")}


def test_every_runtime_import_is_a_runtime_requirement():
    needed = {DIST.get(n, n).lower() for n in _backend_imports()} - OPTIONAL - TRANSITIVE
    assert needed <= _runtime_requirements(), needed - _runtime_requirements()


def test_form_uploads_have_their_parser():
    # FastAPI's File/Form (attachments) need python-multipart at runtime.
    assert "python-multipart" in _runtime_requirements()


def test_heavy_libraries_stay_out_of_the_runtime():
    assert not _runtime_requirements() & {"pandas", "pyarrow", "duckdb", "numpy", "faker", "streamlit",
                                          "matplotlib", "openai"}
