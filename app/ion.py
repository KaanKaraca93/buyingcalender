"""ION API istemcisi — servis hesabi (M3) ve cagiran kullanici token'i (PLM) icin.

M3 tarafi `m3DB/m3_ionapi.py`'nin async karsiligidir; ayni tuzaklar korunur:
  * `maxrecs` MATRIX parametresidir -> `LstFieldValue;maxrecs=300`
  * yazma transaction'lari da GET ile calisir (POST -> 405)
  * HTTP 200 donse bile govdede ErrorMessage olabilir
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .config import IonCredentials, get_ion_credentials, get_settings


class M3ApiError(RuntimeError):
    def __init__(self, message: str, *, program: str = "", transaction: str = "",
                 code: str = "", field: str = "", status: int = 0):
        super().__init__(message)
        self.message = message
        self.program = program
        self.transaction = transaction
        self.code = code
        self.field = field
        self.status = status


def parse_mi_response(body: dict, program: str, transaction: str) -> list[dict[str, str]]:
    """MIRecord[].NameValue[] -> [{Name: Value}]; is-katmani hatasini yukseltir."""
    err_msg = body.get("ErrorMessage") or body.get("Message")
    if body.get("ErrorType") or (err_msg and not body.get("MIRecord")):
        raise M3ApiError(
            f"{program}/{transaction}: {err_msg or 'bilinmeyen hata'}",
            program=program, transaction=transaction,
            code=str(body.get("ErrorCode", "")), field=str(body.get("ErrorField", "")),
        )
    records: list[dict[str, str]] = []
    for rec in body.get("MIRecord", []) or []:
        row = {nv.get("Name"): nv.get("Value", "") for nv in rec.get("NameValue", []) or []}
        records.append(row)
    return records


class IonServiceClient:
    """Servis hesabi ile ION API cagirir (M3 CUSEXTMI). Token'i bellekte cache'ler."""

    def __init__(self, creds: IonCredentials | None = None, *, timeout: float | None = None):
        # DIKKAT: credential burada COZULMEZ. Yanlis/eksik config'de uygulama yine de
        # ayaga kalksin ki /healthz cevap versin ve /readyz hatayi okunabilir sekilde
        # bildirsin. Aksi halde dyno acilista cokup hicbir cevap donmez.
        self._creds = creds
        settings = get_settings()
        self._timeout = timeout or settings.m3_timeout_seconds
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    @property
    def _cfg(self) -> IonCredentials:
        if self._creds is None:
            self._creds = get_ion_credentials()   # hata ilk kullanimda yuzeye cikar
        return self._creds

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # OAuth2 (grant_type=password + service account key/secret)
    # ------------------------------------------------------------------ #
    async def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        async with self._lock:
            if self._token and time.time() < self._token_expiry - 30:
                return self._token
            resp = await self._client.post(
                self._cfg.token_url,
                data={
                    "grant_type": "password",
                    "username": self._cfg.saak,
                    "password": self._cfg.sask,
                    "client_id": self._cfg.client_id,
                    "client_secret": self._cfg.client_secret,
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                raise M3ApiError(
                    f"ION token alinamadi ({resp.status_code}): {resp.text[:200]}",
                    status=resp.status_code,
                )
            payload = resp.json()
            self._token = payload["access_token"]
            self._token_expiry = time.time() + int(payload.get("expires_in", 3600))
            return self._token

    def invalidate_token(self) -> None:
        self._token = None
        self._token_expiry = 0.0

    # ------------------------------------------------------------------ #
    # M3 MI calistirma
    # ------------------------------------------------------------------ #
    async def execute_raw(self, program: str, transaction: str,
                          params: dict[str, Any] | None = None,
                          *, max_recs: int | None = None) -> dict:
        """M3'un ham JSON govdesini dondurur (MIRecord / ErrorMessage dahil).

        Widget'lardaki `_parseM3(resp)` bu govdeyi oldugu gibi ayristirdigi icin
        gecis sirasinda widget tarafinda ayristirma kodu degismek zorunda kalmaz.
        """
        tx = f"{transaction};maxrecs={max_recs}" if max_recs else transaction
        url = f"{self._cfg.ion_url}/{self._cfg.tenant}/M3/m3api-rest/execute/{program}/{tx}"
        query = {k: str(v) for k, v in (params or {}).items() if v is not None and v != ""}

        resp = await self._request(url, query)
        if resp.status_code == 401:
            self.invalidate_token()
            resp = await self._request(url, query)

        if resp.status_code != 200:
            raise M3ApiError(
                f"HTTP {resp.status_code} - {program}/{transaction}: {resp.text[:300]}",
                program=program, transaction=transaction, status=resp.status_code,
            )
        return resp.json()

    async def execute(self, program: str, transaction: str,
                      params: dict[str, Any] | None = None,
                      *, max_recs: int | None = None) -> list[dict[str, str]]:
        body = await self.execute_raw(program, transaction, params, max_recs=max_recs)
        return parse_mi_response(body, program, transaction)

    async def _request(self, url: str, query: dict[str, str]) -> httpx.Response:
        token = await self._ensure_token()
        return await self._client.get(
            url,
            params=query,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )


class IonUserClient:
    """Cagiran kullanicinin kendi Bearer token'i ile ION/PLM cagirir.

    Kimlik ve rol dogrulamasi bilerek kullanicinin kendi yetkisiyle yapilir:
    servis hesabina ekstra PLM yetkisi vermeye gerek kalmaz ve kullanicinin
    gercekten gecerli bir Infor oturumu oldugu kanitlanmis olur.
    """

    def __init__(self, creds: IonCredentials | None = None, *, timeout: float = 20.0):
        self._creds = creds
        self._client = httpx.AsyncClient(timeout=timeout)

    @property
    def _cfg(self) -> IonCredentials:
        if self._creds is None:
            self._creds = get_ion_credentials()
        return self._creds

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, relative_path: str) -> str:
        return f"{self._cfg.ion_url}/{self._cfg.tenant}/{relative_path.lstrip('/')}"

    async def get(self, token: str, relative_path: str) -> httpx.Response:
        return await self._client.get(
            self._url(relative_path),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
