"""Integracion NAGSA: la regla de dependencias de services/ como test.

services/ es logica de aplicacion pura, compartida por el modo local (/api)
y el integrado (/api/v1). Si algun modulo empieza a importar HTTP, la sesion
local o SQLite, deja de ser reutilizable -este test lo detecta en CI en vez
de en una revision de codigo (ver docs/CHECKLIST_INTEGRACION.md, "Regla de
dependencias")."""

import ast
from pathlib import Path

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[1] / "services"

FORBIDDEN_PREFIXES = (
    "fastapi",
    "starlette",
    "sqlite3",
    "app.api",
    "app.local",
    "app.main",
    "app.core.plan_store",
    "app.core.plan_service",
    "app.core.history",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize("path", sorted(SERVICES_DIR.glob("*.py")), ids=lambda p: p.name)
def test_services_module_has_no_forbidden_dependencies(path):
    offending = [name for name in _imports(path) if name.startswith(FORBIDDEN_PREFIXES)]
    assert offending == [], f"{path.name} importa {offending}: services/ no puede depender de HTTP, sesion ni SQLite"
