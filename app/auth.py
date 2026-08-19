"""Cagirani dogrulama ve (varsa) kimligini cozme.

Modlar (AUTH_MODE):
  gateway     : VARSAYILAN. API, ION API Gateway'e custom API olarak tanitilir;
                istekler yalnizca gateway uzerinden gelir. `X-Api-Key` ZORUNLUDUR
                (gateway kaydinda sabit baslik olarak tanimlanir) — boylece Heroku
                URL'i dogrudan cagrilamaz. Kimlik varsa (JWT ya da gateway'in
                ilettigi baslik) okunur ama YALNIZCA denetim kaydi icindir;
                yetkilendirme widget'ta kalir.
  infor_token : Authorization: Bearer <Infor token> -> ION'a `users/me` sorulur.
                Token gecerliyse e-posta alinir, ayni token'la PLM USER sorgusundan
                aktif roller cekilir.
  gateway_jwt : ION API Gateway'in imzaladigi JWT, JWKS ile dogrulanir.
  dev         : dogrulama yok — YALNIZCA yerel gelistirme.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Header, HTTPException, Request

from .config import get_settings
from .policy import Permissions, permissions_for_roles

USERS_ME_PATH = "ifsservice/usermgt/v2/users/me"
PLM_USER_PATH = (
    "FASHIONPLM/odata2/api/odata2/USER"
    "?$filter=Email eq '{email}'&$select=UserId,Name,Email&$top=1"
    "&$expand=UserRoles($filter=IsActive eq true;$select=IsActive;"
    "$expand=Role($select=RoleId,Name))"
)


@dataclass(frozen=True)
class Caller:
    email: str
    name: str
    roles: tuple[str, ...]
    perms: Permissions
    auth_mode: str

    def as_log_dict(self) -> dict:
        return {"user": self.email, "roles": list(self.roles), "admin": self.perms.is_admin}


class _IdentityCache:
    """Token -> Caller. users/me + PLM sorgusunu her istekte tekrarlamamak icin."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Caller]] = {}

    def get(self, key: str) -> Caller | None:
        item = self._data.get(key)
        if not item:
            return None
        expires, caller = item
        if time.time() >= expires:
            self._data.pop(key, None)
            return None
        return caller

    def put(self, key: str, caller: Caller, ttl: int) -> None:
        if len(self._data) > 500:
            self._data.clear()
        self._data[key] = (time.time() + ttl, caller)

    def clear(self) -> None:
        self._data.clear()


identity_cache = _IdentityCache()


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization basligi yok.")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Authorization basligi 'Bearer <token>' olmali.")
    return parts[1].strip()


def _email_from_users_me(body: dict) -> str:
    """ifsservice/usermgt/v2/users/me govdesinden e-posta cikarir."""
    response = (body or {}).get("response") or {}
    userlist = response.get("userlist") or []
    user = userlist[0] if userlist else {}
    emails = user.get("emails") or []
    if emails and isinstance(emails[0], dict) and emails[0].get("value"):
        return str(emails[0]["value"]).strip()
    return str(user.get("id") or "").strip()


def _roles_from_plm_user(body: dict) -> tuple[str, str]:
    """PLM USER govdesinden (ad, roller) cikarir."""
    values = (body or {}).get("value") or []
    user = values[0] if values else {}
    names: list[str] = []
    for ur in user.get("UserRoles") or []:
        role = (ur or {}).get("Role") or {}
        if ur.get("IsActive") and role.get("Name"):
            names.append(str(role["Name"]))
    return str(user.get("Name") or ""), names


async def resolve_caller_from_token(token: str, user_client) -> Caller:
    settings = get_settings()
    cache_key = token[-40:]
    cached = identity_cache.get(cache_key)
    if cached:
        return cached

    resp = await user_client.get(token, USERS_ME_PATH)
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Infor oturumu gecersiz veya suresi dolmus.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Kimlik dogrulanamadi (users/me: {resp.status_code}).")

    email = _email_from_users_me(resp.json())
    if not email:
        raise HTTPException(status_code=401, detail="Kullanici e-postasi cozulemedi.")

    roles: list[str] = []
    name = ""
    plm = await user_client.get(token, PLM_USER_PATH.format(email=quote(email, safe="").replace("'", "%27")))
    if plm.status_code == 200:
        name, roles = _roles_from_plm_user(plm.json())

    caller = Caller(
        email=email,
        name=name,
        roles=tuple(roles),
        perms=permissions_for_roles(roles),
        auth_mode="infor_token",
    )
    identity_cache.put(cache_key, caller, settings.identity_cache_seconds)
    return caller


async def resolve_caller_from_jwt(token: str) -> Caller:
    """ION API Gateway JWT modu — JWKS ile imza dogrulamasi."""
    settings = get_settings()
    if not settings.jwks_url:
        raise HTTPException(status_code=500, detail="JWKS_URL tanimli degil (AUTH_MODE=gateway_jwt).")
    try:
        import jwt  # PyJWT
        from jwt import PyJWKClient
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="PyJWT kurulu degil.") from exc

    try:
        signing_key = PyJWKClient(settings.jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS512"],
            audience=settings.jwt_audience or None,
            issuer=settings.jwt_issuer or None,
            options={"verify_aud": bool(settings.jwt_audience)},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"JWT dogrulanamadi: {exc}") from exc

    email = str(claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "")
    roles = [str(r) for r in (claims.get("roles") or claims.get("groups") or [])]
    return Caller(
        email=email,
        name=str(claims.get("name") or ""),
        roles=tuple(roles),
        perms=permissions_for_roles(roles),
        auth_mode="gateway_jwt",
    )


def _check_api_key(provided: str | None, *, required: bool = False) -> None:
    settings = get_settings()
    if not settings.api_key:
        if required:
            raise HTTPException(
                status_code=500,
                detail="Yapilandirma hatasi: AUTH_MODE=gateway icin API_KEY zorunlu.",
            )
        return
    if not provided or not secrets.compare_digest(provided.strip(), settings.api_key):
        raise HTTPException(status_code=401, detail="Gecersiz X-Api-Key.")


def _identity_from_headers(request: Request) -> tuple[str, str]:
    """Gateway'in ilettigi kimlik basliklari — YALNIZCA denetim kaydi icin.

    Bu deger yetkilendirmede KULLANILMAZ; istemci istedigini yazabilir.
    """
    headers = request.headers
    for key in ("x-infor-user", "x-infor-useremail", "x-caller-email", "x-user-email"):
        value = headers.get(key)
        if value:
            return value.strip(), key
    return "", ""


async def get_caller(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Caller:
    """FastAPI bagimliligi: her korumali endpoint bunu kullanir."""
    settings = get_settings()

    if settings.auth_mode == "gateway":
        _check_api_key(x_api_key, required=True)
        # Kimlik varsa oku (denetim icin). JWKS tanimliysa imzayi da dogrula.
        if authorization and settings.jwks_url:
            try:
                return await resolve_caller_from_jwt(_extract_bearer(authorization))
            except HTTPException:
                pass  # kimlik cozulemedi; istek gateway anahtariyla zaten yetkili
        email, _source = _identity_from_headers(request)
        return Caller(
            email=email or "gateway",
            name="",
            roles=(),
            perms=permissions_for_roles([]),
            auth_mode="gateway",
        )

    _check_api_key(x_api_key)

    if settings.auth_mode == "dev":
        roles = list(settings.dev_identity_roles)
        return Caller(
            email=settings.dev_identity_email,
            name="Dev User",
            roles=tuple(roles),
            perms=permissions_for_roles(roles),
            auth_mode="dev",
        )

    token = _extract_bearer(authorization)
    if settings.auth_mode == "gateway_jwt":
        return await resolve_caller_from_jwt(token)
    return await resolve_caller_from_token(token, request.app.state.user_client)
