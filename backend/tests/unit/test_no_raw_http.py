"""Structural guarantee that outbound HTTP only happens in ``app/clients``.

ARCHITECTURE.md section 1 rule 1. Without this test the rule decays the first time
someone needs "just one quick request" from a service module, and then rate limiting,
caching, retries and the circuit breaker quietly stop applying to that call.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
ALLOWED_PACKAGE = APP_DIR / "clients"

FORBIDDEN_ROOTS = {"httpx", "requests", "urllib3", "aiohttp", "http"}
"""``urllib`` is allowed: robots.txt parsing uses ``urllib.robotparser``, which is
pure parsing and performs no I/O of its own."""


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_only_clients_may_import_http_libraries() -> None:
    offenders: list[str] = []

    for path in APP_DIR.rglob("*.py"):
        if ALLOWED_PACKAGE in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = _imported_roots(tree) & FORBIDDEN_ROOTS
        if forbidden:
            offenders.append(f"{path.relative_to(APP_DIR.parent)}: {sorted(forbidden)}")

    assert offenders == [], (
        "These modules import an HTTP library directly. Every outbound call must go "
        "through a client in app/clients so it inherits the timeout, rate limit, "
        "cache, retry and circuit breaker."
    )


def test_the_check_actually_finds_something(tmp_path: Path) -> None:
    """Guards against the scan silently matching nothing (a wrong path, say)."""
    scanned = [p for p in APP_DIR.rglob("*.py") if ALLOWED_PACKAGE not in p.parents]
    assert len(scanned) > 15

    offending = tmp_path / "bad.py"
    offending.write_text("import httpx\n", encoding="utf-8")
    tree = ast.parse(offending.read_text(encoding="utf-8"))
    assert _imported_roots(tree) & FORBIDDEN_ROOTS == {"httpx"}


def test_clients_package_does_use_httpx() -> None:
    """The rule only means something if the allowed package is where HTTP lives."""
    sources = "\n".join(p.read_text(encoding="utf-8") for p in ALLOWED_PACKAGE.rglob("*.py"))
    assert "import httpx" in sources
