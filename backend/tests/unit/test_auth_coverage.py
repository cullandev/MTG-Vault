"""Structural guarantee that no endpoint ships unauthenticated.

ADR-013 attaches the session dependency to the ``/api`` router rather than to
individual endpoints, so a new route is authenticated by construction. This test is
the enforcement: if someone mounts a router without the dependency, or adds a path to
the allow-list without thinking, the build fails.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.routing import APIRoute

from app.deps import UNAUTHENTICATED_PATHS, require_session
from app.main import create_app

#: Paths that are allowed to be public, each with the reason it is on the list.
EXPECTED_PUBLIC = {
    "/health": "liveness probe for Docker; returns only {status, version}",
    "/ca.crt": "public root certificate; needed precisely when TLS is not yet trusted",
    "/api/auth/login": "cannot require a session to create one",
    "/api/auth/logout": "must work with an already-expired cookie",
    "/api/auth/session": "the SPA boot check; returns a boolean and an expiry",
}


def iter_effective_routes(app: object) -> Iterator[tuple[str, set[str], object]]:
    """Yield ``(path, methods, dependant)`` for every routed endpoint.

    FastAPI keeps included routers as a single wrapper object in ``app.routes`` rather
    than flattening their routes, so walking ``app.routes`` alone finds almost nothing.
    This resolves the wrappers, which is the whole point of the test below.
    """
    for route in app.routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute):
            yield route.path, set(route.methods or ()), route.dependant
        elif hasattr(route, "effective_route_contexts"):
            for context in route.effective_route_contexts():
                yield context.path, set(context.methods or ()), context.dependant


def _dependency_calls(dependant: object) -> set[object]:
    return {dependency.call for dependency in dependant.dependencies}  # type: ignore[attr-defined]


def test_allow_list_matches_the_declared_constant() -> None:
    """Changing the allow-list has to be a deliberate edit to this test, too."""
    assert set(EXPECTED_PUBLIC) == set(UNAUTHENTICATED_PATHS)


def test_every_route_is_authenticated_or_explicitly_public(settings: object) -> None:
    app = create_app()
    unprotected: list[str] = []

    for path, methods, dependant in iter_effective_routes(app):
        if path in UNAUTHENTICATED_PATHS:
            continue
        if path == "/{full_path:path}":
            # SPA fallback: serves the static shell, which contains no user data.
            continue
        if require_session not in _dependency_calls(dependant):
            unprotected.append(f"{sorted(methods)} {path}")

    assert unprotected == [], (
        "These routes are reachable without a session. Mount them under the "
        "authenticated /api router, or add them to UNAUTHENTICATED_PATHS *and* to "
        "EXPECTED_PUBLIC in this test with a written reason."
    )


def test_api_routes_exist_at_all(settings: object) -> None:
    """Guards against the previous test passing because nothing was registered."""
    app = create_app()
    api_paths = [path for path, _, _ in iter_effective_routes(app) if path.startswith("/api/")]
    assert len(api_paths) > 10
