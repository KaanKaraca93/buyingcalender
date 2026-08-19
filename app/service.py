"""M3 CUSEXTMI okuma/yazma is mantigi.

Upsert deseni `m3DB/cugex.py` + widget'lardan tasindi:
  * varsayilan strateji `chg_first`  -> once ChgFieldValue, "kayit yok" ise AddFieldValue
    (kismi guncelleme icin dogru olan: tek bir tarihi degistirirken diger alanlar silinmez)
  * `add_first` -> once AddFieldValue, "already exist" ise ChgFieldValue
    (Excel'den toplu ilk yukleme icin daha az cagri)
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .ion import M3ApiError
from .policy import PROGRAM, TableSpec

# M3 hata mesaji dil/versiyona gore degisebilir -> desen listesi ile eslesiyoruz.
# NOT: gercek ortamda dogrulanmali (bkz. README "Netlesmesi gerekenler").
ALREADY_EXISTS_PATTERNS = re.compile(
    r"already exist|zaten (var|mevcut)|duplicate|kayit var", re.IGNORECASE
)
NOT_FOUND_PATTERNS = re.compile(
    r"not found|does not exist|no record|record not|bulunamadi|kayit yok|kayit bulunamadi",
    re.IGNORECASE,
)

Strategy = Literal["chg_first", "add_first"]


@dataclass
class RowResult:
    index: int
    keys: dict[str, str]
    ok: bool
    action: str = ""          # added | changed | skipped
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"index": self.index, "keys": self.keys, "ok": self.ok}
        if self.action:
            out["action"] = self.action
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class UpsertOutcome:
    results: list[RowResult] = field(default_factory=list)
    remaining: list[int] = field(default_factory=list)   # sureye takilip islenmeyen satirlar
    added: int = 0
    changed: int = 0
    failed: int = 0


async def list_records(client, spec: TableSpec, keys: dict[str, str],
                       *, max_recs: int) -> list[dict[str, str]]:
    params: dict[str, Any] = {"FILE": spec.name}
    params.update({k: v for k, v in keys.items() if str(v).strip() != ""})
    return await client.execute(PROGRAM, "LstFieldValue", params, max_recs=max_recs)


async def upsert_row(client, spec: TableSpec, keys: dict[str, str],
                     values: dict[str, str], *, strategy: Strategy = "chg_first",
                     create_values: dict[str, str] | None = None) -> str:
    """Tek satiri upsert eder; 'added' veya 'changed' dondurur."""
    base = {"FILE": spec.name, **keys}
    full = {**(create_values or {}), **values}

    if strategy == "add_first":
        first_tx, first_payload = "AddFieldValue", full
        second_tx, second_payload = "ChgFieldValue", values
        fallback_pattern = ALREADY_EXISTS_PATTERNS
        first_action, second_action = "added", "changed"
    else:
        first_tx, first_payload = "ChgFieldValue", values
        second_tx, second_payload = "AddFieldValue", full
        fallback_pattern = NOT_FOUND_PATTERNS
        first_action, second_action = "changed", "added"

    try:
        await client.execute(PROGRAM, first_tx, {**base, **first_payload}, max_recs=None)
        return first_action
    except M3ApiError as exc:
        if not fallback_pattern.search(str(exc)):
            raise
    await client.execute(PROGRAM, second_tx, {**base, **second_payload}, max_recs=None)
    return second_action


async def upsert_rows(client, spec: TableSpec, rows: list[dict[str, Any]],
                      *, strategy: Strategy = "chg_first", concurrency: int = 6,
                      deadline_seconds: float = 20.0) -> UpsertOutcome:
    """Satirlari sinirli es zamanlilikla upsert eder.

    Heroku router istegi 30 sn'de keser; `deadline_seconds` dolunca kalan satirlar
    islenmeden `remaining` icinde geri bildirilir — istemci kaldigi yerden devam eder.
    """
    outcome = UpsertOutcome()
    started = time.monotonic()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[RowResult | None] = [None] * len(rows)
    timed_out: list[int] = []

    async def run(index: int, row: dict[str, Any]) -> None:
        keys = {k: str(v) for k, v in (row.get("keys") or {}).items()}
        if time.monotonic() - started > deadline_seconds:
            timed_out.append(index)
            return
        async with semaphore:
            if time.monotonic() - started > deadline_seconds:
                timed_out.append(index)
                return
            try:
                action = await upsert_row(
                    client, spec, keys,
                    {k: str(v) for k, v in (row.get("values") or {}).items()},
                    strategy=strategy,
                    create_values={k: str(v) for k, v in (row.get("create_values") or {}).items()},
                )
                results[index] = RowResult(index=index, keys=keys, ok=True, action=action)
            except M3ApiError as exc:
                results[index] = RowResult(index=index, keys=keys, ok=False, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                results[index] = RowResult(index=index, keys=keys, ok=False,
                                           error=f"beklenmeyen hata: {exc}")

    await asyncio.gather(*(run(i, r) for i, r in enumerate(rows)))

    for item in results:
        if item is None:
            continue
        outcome.results.append(item)
        if not item.ok:
            outcome.failed += 1
        elif item.action == "added":
            outcome.added += 1
        else:
            outcome.changed += 1
    outcome.remaining = sorted(timed_out)
    return outcome
