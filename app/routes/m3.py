"""M3 takvim endpoint'leri.

Uc katman sunuluyor:
  /v1/m3/exec    — M3'e birebir gecis (widget'taki cagriyi neredeyse kopyala-yapistir
                   tasimak icin; ham MIRecord govdesi doner)
  /v1/m3/list    — LstFieldValue icin sadelestirilmis ucu
  /v1/m3/upsert  — coklu satir upsert (Chg->Add veya Add->Chg)
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import audit
from ..auth import Caller, get_caller
from ..config import get_settings
from ..ion import M3ApiError, m3_error_of
from ..policy import (
    PROGRAM,
    READ_TRANSACTIONS,
    PolicyError,
    allowed_transactions,
    check_write_allowed,
    get_table,
)
from ..schemas import (
    ExecRequest,
    ListRequest,
    ListResponse,
    MeResponse,
    UpsertRequest,
    UpsertResponse,
)
from ..service import list_records, upsert_rows

router = APIRouter(prefix="/v1", tags=["m3"])

PK_FIELDS = tuple(f"PK0{i}" for i in range(1, 9))


def _table(name: str):
    return get_table(name, extra_files=get_settings().extra_files)


@router.get("/me", response_model=MeResponse)
async def me(caller: Caller = Depends(get_caller)) -> MeResponse:
    """Bilgilendirme amaclidir. Yetkilendirme widget'ta kalir (ENFORCE_ROLE_POLICY=0)."""
    settings = get_settings()
    return MeResponse(
        email=caller.email,
        name=caller.name,
        roles=list(caller.roles),
        is_admin=caller.perms.is_admin,
        editable_depts=sorted(caller.perms.editable_depts),
        role_labels=list(caller.perms.labels),
        auth_mode=caller.auth_mode,
        read_only=settings.read_only,
        enforce_role_policy=settings.enforce_role_policy,
    )


@router.get("/tables")
async def tables(caller: Caller = Depends(get_caller)) -> dict:
    from ..policy import TABLES

    settings = get_settings()
    return {
        "tables": [
            {
                "name": spec.name,
                "pk_fields": list(spec.pk_fields),
                "required_pk": list(spec.required_pk),
                "writable_fields": sorted(spec.writable_fields),
                "description": spec.description,
            }
            for spec in TABLES.values()
        ],
        "extra_files": settings.extra_files,
        "transactions": sorted(allowed_transactions(allow_delete=settings.allow_delete)),
        "enforce_role_policy": settings.enforce_role_policy,
    }


# --------------------------------------------------------------------------- #
# 1. Birebir gecis ucu — widget migrasyonu icin
# --------------------------------------------------------------------------- #
@router.post("/m3/exec")
async def m3_exec(payload: ExecRequest, request: Request,
                  caller: Caller = Depends(get_caller)) -> dict:
    """CUSEXTMI transaction'ini oldugu gibi calistirir, M3'un ham govdesini dondurur.

    Widget'taki
        M3_EXEC + "/LstFieldValue;maxrecs=300?FILE=CPSTAKVIM&PK01=11"
    cagrisinin karsiligi:
        POST /v1/m3/exec {"transaction":"LstFieldValue","maxrecs":300,
                          "params":{"FILE":"CPSTAKVIM","PK01":"11"}}
    Yanit `{"MIRecord":[...]}` oldugu icin widget'taki `_parseM3` degismeden calisir.
    """
    settings = get_settings()
    tx = payload.transaction.strip()
    params = {k.upper(): str(v) for k, v in payload.params.items()}

    # maxrecs yalnizca okuma transaction'larinda anlamli; yazmada gonderilirse
    # M3 tarafinda gereksiz matrix parametresi olusur.
    max_recs = None
    if payload.maxrecs and tx in READ_TRANSACTIONS:
        max_recs = min(payload.maxrecs, settings.max_maxrecs)

    return await _run_exec(request, caller, tx, params, max_recs)


async def _run_exec(request: Request, caller: Caller, tx: str,
                    params: dict[str, str], max_recs: int | None) -> dict:
    """`/v1/m3/exec` (POST) ve `/v1/m3/x/...` (GET) ucunun ortak govdesi."""
    settings = get_settings()

    if tx not in allowed_transactions(allow_delete=settings.allow_delete):
        raise HTTPException(status_code=400, detail=f"Izin verilmeyen transaction: {tx}")
    if settings.read_only and tx not in READ_TRANSACTIONS:
        raise HTTPException(status_code=503, detail="Servis salt-okunur modda (READ_ONLY=1).")

    file_name = params.get("FILE", "")
    try:
        spec = _table(file_name)
    except PolicyError as exc:
        audit.log_denied(caller.email, str(exc), table=file_name)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    keys = {k: v for k, v in params.items() if k in PK_FIELDS}
    values = {k: v for k, v in params.items() if k not in PK_FIELDS and k != "FILE"}

    if tx not in READ_TRANSACTIONS:
        try:
            spec.validate_values(values)
            if settings.enforce_role_policy:
                check_write_allowed(spec, values, caller.perms,
                                    dept=values.get(spec.dept_field or ""))
        except PolicyError as exc:
            audit.log_denied(caller.email, str(exc), table=spec.name)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            audit.log_denied(caller.email, str(exc), table=spec.name)
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    if tx not in READ_TRANSACTIONS:
        max_recs = None

    started = time.monotonic()
    status, body = await request.app.state.m3.execute_passthrough(
        PROGRAM, tx, {"FILE": spec.name, **keys, **values}, max_recs=max_recs
    )

    elapsed = int((time.monotonic() - started) * 1000)
    m3_err = m3_error_of(body)
    audit.log_event(
        "m3.exec", user=caller.email, table=spec.name, tx=tx,
        keys=keys, ms=elapsed, status=status,
        records=len(body.get("MIRecord") or []) if isinstance(body, dict) else 0,
        m3_error_code=m3_err[0] if m3_err else None,
        m3_error=m3_err[1] if m3_err else None,
    )

    # M3'un durum kodu ve govdesi AYNEN aktarilir - widget'in hata mantigi bozulmasin.
    # Ek olarak hata varsa BASLIGA koyariz: govde/durum degismedigi icin widget'i
    # etkilemez, ama gateway/log tarafinda sessiz basarisizlik gorunur olur.
    headers = {}
    if m3_err:
        headers["X-M3-Error-Code"] = m3_err[0] or "UNKNOWN"
        headers["X-M3-Error"] = m3_err[1][:180]
    return JSONResponse(status_code=status, content=body, headers=headers)


# --------------------------------------------------------------------------- #
# 1b. M3 URL'inin BIREBIR taklidi — widget'ta tek satir degisir
# --------------------------------------------------------------------------- #
@router.get("/m3/x/{transaction:path}")
async def m3_passthrough(transaction: str, request: Request,
                         caller: Caller = Depends(get_caller)) -> dict:
    """M3'un kendi URL semasini taklit eder; GET + query string.

    Widget'taki tek degisiklik:
        M3_EXEC = "M3/m3api-rest/execute/CUSEXTMI"
        M3_EXEC = "TAKVIMAPI/v1/m3/x"                <- yalnizca bu satir

    Geri kalan her sey ayni kalir:
        M3_EXEC + "/LstFieldValue;maxrecs=300?FILE=CPSTAKVIM&PK01=11"
        M3_EXEC + "/ChgFieldValue?FILE=KOLONCESI&PK01=...&A230=2026-03-12"

    `;maxrecs=` matrix parametresi, metod (GET) ve yanit govdesi birebir korunur;
    boylece widget'ta `_qs`, `_parseM3` ve hata yonetimi degismek zorunda kalmaz.
    """
    settings = get_settings()

    # "LstFieldValue;maxrecs=300" -> tx + matrix parametreleri
    raw = transaction.strip().strip("/")
    parts = raw.split(";")
    tx = parts[0].strip()
    max_recs = None
    for extra in parts[1:]:
        name, _, value = extra.partition("=")
        if name.strip().lower() == "maxrecs" and value.strip().isdigit():
            max_recs = min(int(value.strip()), settings.max_maxrecs)

    params = {k.upper(): v for k, v in request.query_params.items()}
    return await _run_exec(request, caller, tx, params, max_recs)


# --------------------------------------------------------------------------- #
# 2. Sadelestirilmis okuma
# --------------------------------------------------------------------------- #
@router.post("/m3/list", response_model=ListResponse)
async def m3_list(payload: ListRequest, request: Request,
                  caller: Caller = Depends(get_caller)) -> ListResponse:
    settings = get_settings()
    try:
        spec = _table(payload.file)
        spec.validate_keys(payload.keys, for_write=False)
    except PolicyError as exc:
        audit.log_denied(caller.email, str(exc), table=payload.file)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    max_recs = min(payload.maxrecs or settings.default_maxrecs, settings.max_maxrecs)
    started = time.monotonic()
    try:
        records = await list_records(request.app.state.m3, spec, payload.keys, max_recs=max_recs)
    except M3ApiError as exc:
        raise HTTPException(status_code=502, detail=f"M3 hatasi: {exc}") from exc

    elapsed = int((time.monotonic() - started) * 1000)
    audit.log_read(caller.email, spec.name, payload.keys, len(records), elapsed)
    return ListResponse(
        file=spec.name,
        count=len(records),
        records=records,
        truncated=len(records) >= max_recs,
    )


# --------------------------------------------------------------------------- #
# 3. Coklu satir upsert
# --------------------------------------------------------------------------- #
@router.post("/m3/upsert", response_model=UpsertResponse)
async def m3_upsert(payload: UpsertRequest, request: Request,
                    caller: Caller = Depends(get_caller)) -> UpsertResponse:
    settings = get_settings()
    if settings.read_only:
        raise HTTPException(status_code=503, detail="Servis salt-okunur modda (READ_ONLY=1).")
    if not payload.rows:
        raise HTTPException(status_code=400, detail="rows bos olamaz.")
    if len(payload.rows) > settings.max_rows_per_request:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Tek istekte en fazla {settings.max_rows_per_request} satir gonderilebilir "
                f"(gelen: {len(payload.rows)}). Istemci parcalara bolmelidir."
            ),
        )

    try:
        spec = _table(payload.file)
        for row in payload.rows:
            spec.validate_keys(row.keys, for_write=True)
            spec.validate_values(row.values)
            spec.validate_values(row.create_values)
            if settings.enforce_role_policy:
                dept = (row.values.get(spec.dept_field or "")
                        or row.create_values.get(spec.dept_field or ""))
                check_write_allowed(spec, {**row.create_values, **row.values},
                                    caller.perms, dept=dept)
    except PolicyError as exc:
        audit.log_denied(caller.email, str(exc), table=payload.file)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        audit.log_denied(caller.email, str(exc), table=payload.file, roles=list(caller.roles))
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    started = time.monotonic()
    outcome = await upsert_rows(
        request.app.state.m3,
        spec,
        [row.model_dump() for row in payload.rows],
        strategy=payload.strategy,
        concurrency=settings.write_concurrency,
        deadline_seconds=settings.request_deadline_seconds,
    )
    elapsed = int((time.monotonic() - started) * 1000)
    audit.log_write(caller.email, spec.name, outcome.added, outcome.changed,
                    outcome.failed, len(outcome.remaining), elapsed, payload.strategy)

    return UpsertResponse(
        file=spec.name,
        added=outcome.added,
        changed=outcome.changed,
        failed=outcome.failed,
        remaining=outcome.remaining,
        results=[r.as_dict() for r in outcome.results],
    )
