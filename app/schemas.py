"""Istek/yanit modelleri."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _stringify(value: Any) -> dict[str, str]:
    """M3 her seyi string olarak alir; widget ise sayi gonderebiliyor.

    Ornek: koleksiyon-oncesi widget AddFieldValue'da `N096: row.sira` (sayi) ve
    `N196: 1` gonderiyor. JSON'a sayi olarak gectigi icin katı `dict[str, str]`
    dogrulamasi 422 verirdi. Burada skaler degerler stringe cevrilir; nesne/dizi
    yine reddedilir.
    """
    if not isinstance(value, dict):
        return value
    out: dict[str, str] = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, bool):
            out[str(key)] = "1" if item else "0"
        elif isinstance(item, (int, float, str)):
            out[str(key)] = str(item)
        else:
            raise ValueError(f"{key}: yalnizca metin/sayi deger gonderilebilir")
    return out


class ExecRequest(BaseModel):
    """CUSEXTMI'a birebir gecis. Widget'taki URL parametreleri `params` icine konur."""

    transaction: str = Field(..., description="LstFieldValue | GetFieldValue | AddFieldValue | ChgFieldValue")
    params: dict[str, str] = Field(..., description="FILE + PK01..PK08 + alanlar")
    maxrecs: int | None = Field(default=None, ge=1, description="MATRIX parametresi (yalnizca Lst/Get icin anlamli)")

    _norm = field_validator("params", mode="before")(_stringify)


class ListRequest(BaseModel):
    file: str = Field(..., description="Tablo adi: KOLONCESI | CPSTAKVIM | TEMATAKVIM")
    keys: dict[str, str] = Field(default_factory=dict, description="PK01..PK08 (bos birakilanlar enumerate edilir)")
    maxrecs: int | None = Field(default=None, ge=1, description="MATRIX parametresi; ust sinir MAX_MAXRECS")

    _norm = field_validator("keys", mode="before")(_stringify)


class ListResponse(BaseModel):
    file: str
    count: int
    records: list[dict[str, str]]
    truncated: bool = Field(default=False, description="Donen kayit sayisi maxrecs'e esitse True — daha fazlasi olabilir")


class UpsertRow(BaseModel):
    keys: dict[str, str]
    values: dict[str, str] = Field(default_factory=dict, description="Guncellenecek alanlar")
    create_values: dict[str, str] = Field(default_factory=dict, description="Kayit yoksa ek olarak yazilacak alanlar")

    _norm = field_validator("keys", "values", "create_values", mode="before")(_stringify)


class UpsertRequest(BaseModel):
    file: str
    rows: list[UpsertRow]
    strategy: Literal["chg_first", "add_first"] = "chg_first"


class UpsertResponse(BaseModel):
    file: str
    added: int
    changed: int
    failed: int
    remaining: list[int] = Field(default_factory=list, description="Sure siniri nedeniyle islenmeyen satir indeksleri")
    results: list[dict[str, Any]]


class MeResponse(BaseModel):
    email: str
    name: str
    roles: list[str]
    is_admin: bool
    editable_depts: list[str]
    role_labels: list[str]
    auth_mode: str
    read_only: bool
    enforce_role_policy: bool = False
