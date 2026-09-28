"""FastAPI application factory for the ViralClip backend.

Minimal for the scaffold baseline: CORS, a root info endpoint, and a health
check. The job store is initialised on startup. No database/redis/queue.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, jobstore
from .config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobstore.init_store()
    yield


def create_app() -> FastAPI:
    config = get_config()
    app = FastAPI(title="ViralClip", version=__version__, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "ViralClip", "version": __version__, "status": "ok"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
