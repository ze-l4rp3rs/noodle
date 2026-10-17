"""The local/hosted HTTP entry point (07, step 15).

``scripts/serve.sh`` (and any container host) runs this. It is a thin wrapper:
:func:`create_server` builds the runtime from config and hands it to
:func:`noodle.api.create_app`, so all request handling stays in one place and a
test can inject a runtime instead of building one from the environment.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI

from .api import create_app
from .config import Settings, load_settings
from .service import Runtime, build_runtime


def create_server(
    runtime: Optional[Runtime] = None, settings: Optional[Settings] = None
) -> FastAPI:
    """Return the ASGI app, building the runtime from config when none is given."""

    if runtime is None:
        runtime = build_runtime(settings if settings is not None else load_settings())
    return create_app(runtime)


__all__ = ["create_server"]
