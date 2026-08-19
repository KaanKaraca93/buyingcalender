"""Uygulama ayarlari — tamami ortam degiskeninden okunur (Heroku Config Vars).

Hicbir sir kaynak kodda tutulmaz. Bkz. .env.example
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on", "evet")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = _env(name)
    if not raw:
        return list(default or [])
    return [p.strip() for p in raw.split(",") if p.strip()]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class IonCredentials:
    """.ionapi service-account dosyasinin icerigi."""

    tenant: str          # ti
    client_id: str       # ci
    client_secret: str   # cs
    ion_url: str         # iu  (https://mingle-ionapi.eu1.inforcloudsuite.com)
    token_url: str       # pu + ot
    saak: str            # service account access key  -> OAuth username
    sask: str            # service account secret key  -> OAuth password

    @classmethod
    def from_ionapi_dict(cls, data: dict) -> "IonCredentials":
        missing = [k for k in ("ti", "ci", "cs", "iu", "pu", "ot", "saak", "sask") if not data.get(k)]
        if missing:
            raise ConfigError(f".ionapi icerigi eksik alanlar: {', '.join(missing)}")
        return cls(
            tenant=data["ti"],
            client_id=data["ci"],
            client_secret=data["cs"],
            ion_url=str(data["iu"]).rstrip("/"),
            token_url=str(data["pu"]).rstrip("/") + "/" + str(data["ot"]).lstrip("/"),
            saak=data["saak"],
            sask=data["sask"],
        )

    @classmethod
    def load(cls) -> "IonCredentials":
        """Sirasiyla dener: IONAPI_B64 -> IONAPI_JSON -> IONAPI_FILE -> tekil env'ler."""
        b64 = _env("IONAPI_B64")
        if b64:
            try:
                raw = base64.b64decode(b64).decode("utf-8-sig")
            except Exception as exc:  # noqa: BLE001
                raise ConfigError(f"IONAPI_B64 cozulemedi: {exc}") from exc
            return cls.from_ionapi_dict(json.loads(raw))

        raw_json = _env("IONAPI_JSON")
        if raw_json:
            return cls.from_ionapi_dict(json.loads(raw_json))

        path = _env("IONAPI_FILE")
        if path:
            with open(path, "r", encoding="utf-8-sig") as fh:
                return cls.from_ionapi_dict(json.load(fh))

        # Tekil degiskenler (Heroku panelinden elle girmek isteyenler icin)
        if _env("ION_TENANT"):
            return cls(
                tenant=_env("ION_TENANT"),
                client_id=_env("ION_CLIENT_ID"),
                client_secret=_env("ION_CLIENT_SECRET"),
                ion_url=_env("ION_URL").rstrip("/"),
                token_url=_env("ION_TOKEN_URL"),
                saak=_env("ION_SAAK"),
                sask=_env("ION_SASK"),
            )

        raise ConfigError(
            "ION kimlik bilgisi bulunamadi. IONAPI_B64 (onerilen) veya IONAPI_JSON / "
            "IONAPI_FILE / ION_* degiskenlerinden birini tanimlayin."
        )


@dataclass(frozen=True)
class Settings:
    # --- kimlik dogrulama ---
    # gateway     : ION API Gateway arkasinda calisir; X-Api-Key ZORUNLU.
    #               Kimlik varsa (JWT/baslik) sadece denetim kaydi icin okunur.
    # infor_token : cagiranin Bearer token'i ION'a sorulur (users/me)
    # gateway_jwt : ION API Gateway'in imzaladigi JWT, JWKS ile dogrulanir
    # dev         : kimlik dogrulama YOK - sadece yerel gelistirme
    auth_mode: str = "gateway"
    api_key: str = ""                     # opsiyonel ikinci katman (X-Api-Key)
    jwks_url: str = ""                    # gateway_jwt modu icin
    jwt_audience: str = ""
    jwt_issuer: str = ""
    dev_identity_email: str = ""
    dev_identity_roles: list[str] = field(default_factory=list)

    # --- ag / CORS ---
    allowed_origins: list[str] = field(default_factory=list)

    # --- istek sinirlari (Heroku router 30 sn'de keser) ---
    max_rows_per_request: int = 50
    request_deadline_seconds: float = 20.0
    write_concurrency: int = 6
    m3_timeout_seconds: float = 30.0
    default_maxrecs: int = 300
    max_maxrecs: int = 1000

    # --- davranis ---
    # Rol/departman kontrolu SUNUCUDA uygulansin mi. Varsayilan KAPALI:
    # yetkilendirme widget'larda kalir, bu API onlarin M3 cagrisinin yerini alir.
    enforce_role_policy: bool = False
    allow_delete: bool = False            # DelFieldValue ucunu ac (varsayilan kapali)
    extra_files: list[str] = field(default_factory=list)  # ek CUGEX tablolari
    read_only: bool = False               # acil durum kill-switch
    identity_cache_seconds: int = 300
    log_level: str = "INFO"

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            auth_mode=_env("AUTH_MODE", "gateway").lower(),
            api_key=_env("API_KEY"),
            jwks_url=_env("JWKS_URL"),
            jwt_audience=_env("JWT_AUDIENCE"),
            jwt_issuer=_env("JWT_ISSUER"),
            dev_identity_email=_env("DEV_IDENTITY_EMAIL", "dev@local"),
            dev_identity_roles=_env_list("DEV_IDENTITY_ROLES", ["IT Admin"]),
            allowed_origins=_env_list("ALLOWED_ORIGINS"),
            max_rows_per_request=_env_int("MAX_ROWS_PER_REQUEST", 50),
            request_deadline_seconds=float(_env_int("REQUEST_DEADLINE_SECONDS", 20)),
            write_concurrency=_env_int("WRITE_CONCURRENCY", 6),
            m3_timeout_seconds=float(_env_int("M3_TIMEOUT_SECONDS", 30)),
            default_maxrecs=_env_int("DEFAULT_MAXRECS", 300),
            max_maxrecs=_env_int("MAX_MAXRECS", 1000),
            enforce_role_policy=_env_bool("ENFORCE_ROLE_POLICY", False),
            allow_delete=_env_bool("ALLOW_DELETE", False),
            extra_files=[f.upper() for f in _env_list("EXTRA_FILES")],
            read_only=_env_bool("READ_ONLY", False),
            identity_cache_seconds=_env_int("IDENTITY_CACHE_SECONDS", 300),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()


@lru_cache(maxsize=1)
def get_ion_credentials() -> IonCredentials:
    return IonCredentials.load()
