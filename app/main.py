"""Buying Takvim — M3 Proxy API.

PLM widget'lari M3'e dogrudan gidiyordu; bu da her kullaniciya M3 yetkisi
gerektiriyordu. Bu servis araya girer ve M3'e servis hesabiyla gider.

Yetkilendirme WIDGET'LARDA kalir — bu API mevcut M3 cagrisinin yerine gecer,
davranisi degistirmez. Sunucunun yaptigi: ION Gateway anahtarini dogrulamak,
izin verilen tablo/alan/transaction disina cikmamak ve denetim kaydi tutmak.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import audit
from .config import ConfigError, get_settings
from .ion import IonServiceClient, IonUserClient, M3ApiError
from .policy import PermissionError_, PolicyError
from .routes.m3 import router as m3_router

VERSION = "0.1.0"


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    app.state.m3 = IonServiceClient()
    app.state.user_client = IonUserClient()
    audit.log_event("startup", version=VERSION, auth_mode=settings.auth_mode,
                    read_only=settings.read_only)
    try:
        yield
    finally:
        await app.state.m3.aclose()
        await app.state.user_client.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Buying Takvim M3 API",
        version=VERSION,
        description="PLM takvim widget'lari icin M3 CUSEXTMI proxy'si",
        lifespan=lifespan,
        docs_url="/docs" if settings.auth_mode == "dev" else None,
        redoc_url=None,
        # OpenAPI semasi her zaman acik: ION API Gateway'e custom API tanitirken
        # gerekiyor ve icinde sir yok. Interaktif /docs yalnizca dev modda.
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins or [],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
        max_age=600,
    )

    @app.exception_handler(PolicyError)
    async def _policy_error(_request, exc: PolicyError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PermissionError_)
    async def _permission_error(_request, exc: PermissionError_):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ConfigError)
    async def _config_error(_request, exc: ConfigError):
        return JSONResponse(status_code=500, content={"detail": f"Yapilandirma hatasi: {exc}"})

    @app.exception_handler(M3ApiError)
    async def _m3_error(_request, exc: M3ApiError):
        return JSONResponse(status_code=502, content={"detail": f"M3 hatasi: {exc}"})

    @app.exception_handler(httpx.HTTPError)
    async def _upstream_error(_request, exc: httpx.HTTPError):
        # DNS/baglanti/zaman asimi — istemciye 500 degil, "yukari akis" hatasi donmeli
        return JSONResponse(
            status_code=502,
            content={"detail": f"ION'a ulasilamadi: {type(exc).__name__}: {exc}"},
        )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        """Kimlik dogrulamasi gerektirmez — Heroku/uptime kontrolu icin."""
        return {"status": "ok", "version": VERSION}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict:
        """ION token alinabiliyor mu — canliya alirken ilk bakilacak yer."""
        try:
            await app.state.m3._ensure_token()  # noqa: SLF001
            return {"status": "ready", "ion": "ok"}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "not-ready", "error": str(exc)})

    app.include_router(m3_router)
    return app


app = create_app()
