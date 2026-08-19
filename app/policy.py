"""Izin listesi ve (opsiyonel) rol kurallari.

Bu dosya API'nin kapsam sinirini cizer: proxy GENEL AMACLI DEGILDIR.
Sadece burada tanimli (veya EXTRA_FILES ile eklenmis) tablolar, PK'lar,
alanlar ve transaction'lar gecebilir.

ONEMLI — yetkilendirme nerede:
  Rol/departman kontrolu VARSAYILAN OLARAK KAPALIDIR (`ENFORCE_ROLE_POLICY=0`).
  Yetkilendirme widget'larda kalir; bu API yalnizca widget'in M3 cagrisinin
  yerine gecer, davranisini degistirmez. Asagidaki ROLE_RULES / check_write_allowed
  ileride sunucu tarafinda kontrol istenirse diye hazir durur, kendiliginden
  devreye girmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PROGRAM = "CUSEXTMI"

READ_TRANSACTIONS = {"LstFieldValue", "GetFieldValue"}
WRITE_TRANSACTIONS = {"AddFieldValue", "AddFieldValueEx", "ChgFieldValue"}
DELETE_TRANSACTIONS = {"DelFieldValue"}
# DelFieldValue varsayilan olarak KAPALI; ALLOW_DELETE=1 ile acilir.


def allowed_transactions(*, allow_delete: bool = False) -> set[str]:
    txs = READ_TRANSACTIONS | WRITE_TRANSACTIONS
    if allow_delete:
        txs = txs | DELETE_TRANSACTIONS
    return txs

TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
    "ş": "s", "Ş": "s", "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g",
})


def norm_role(value: str | None) -> str:
    """Widget'taki _normRole ile ayni: kucuk harf + Turkce karakter sadelestirme."""
    return re.sub(r"\s+", " ", (value or "").strip().lower().translate(TR_MAP))


# --------------------------------------------------------------------------- #
# Roller
# --------------------------------------------------------------------------- #
# Kaynak: koleksiyon-oncesi-takvim-widget ROLE_RULES (v3.6)
# depts == "ALL" -> tum departmanlar (IT Admin)
ROLE_RULES: list[tuple[re.Pattern[str], str | tuple[str, ...], str]] = [
    (re.compile(r"admin|administrator|it\s*admin"), "ALL", "IT Admin"),
    (re.compile(r"kumas"), ("Kumaş Tasarım",), "Kumaş Tedarik & Planlama"),
    (re.compile(r"sourc|merchand"), ("Sourcing",), "Merchandiser"),
    (re.compile(r"urun\s*yonet|product\s*manag"), ("Ürün Yönetimi", "UY-Tasarım"), "Ürün Yöneticisi"),
    (re.compile(r"tasar|design"), ("Tasarım", "UY-Tasarım", "Kumaş Tasarım"), "Tasarımcı"),
]


@dataclass(frozen=True)
class Permissions:
    is_admin: bool
    editable_depts: frozenset[str]
    labels: tuple[str, ...]

    def can_edit_dept(self, dept: str | None) -> bool:
        if self.is_admin:
            return True
        return bool(dept) and dept in self.editable_depts


def permissions_for_roles(role_names: list[str]) -> Permissions:
    depts: set[str] = set()
    labels: list[str] = []
    is_admin = False
    for name in role_names or []:
        norm = norm_role(name)
        for pattern, rule_depts, label in ROLE_RULES:
            if pattern.search(norm):
                if rule_depts == "ALL":
                    is_admin = True
                else:
                    depts.update(rule_depts)
                if label not in labels:
                    labels.append(label)
                break  # rol basina ilk eslesen kural
    return Permissions(is_admin=is_admin, editable_depts=frozenset(depts), labels=tuple(labels))


# --------------------------------------------------------------------------- #
# Tablolar
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TableSpec:
    name: str
    pk_fields: tuple[str, ...]
    required_pk: tuple[str, ...]
    readable: bool = True
    writable_fields: frozenset[str] = frozenset()
    admin_only_fields: frozenset[str] = frozenset()
    #: satirin hangi alaninda departman bilgisi tutuluyor (departman bazli yetki icin)
    dept_field: str | None = None
    #: yazma tamamen IT Admin'e mi kisitli
    admin_only_write: bool = False
    description: str = ""

    def validate_keys(self, keys: dict[str, str], *, for_write: bool) -> None:
        unknown = [k for k in keys if k not in self.pk_fields]
        if unknown:
            raise PolicyError(f"{self.name}: bilinmeyen PK alani: {', '.join(sorted(unknown))}")
        if for_write:
            missing = [k for k in self.pk_fields if not str(keys.get(k, "")).strip()]
            if missing:
                raise PolicyError(
                    f"{self.name}: yazma icin tum PK alanlari zorunlu, eksik: {', '.join(missing)}"
                )
        else:
            missing = [k for k in self.required_pk if not str(keys.get(k, "")).strip()]
            if missing:
                raise PolicyError(
                    f"{self.name}: okuma icin zorunlu PK eksik: {', '.join(missing)}"
                )

    def validate_values(self, values: dict[str, str]) -> None:
        unknown = [k for k in values if k not in self.writable_fields]
        if unknown:
            raise PolicyError(
                f"{self.name}: yazilamaz alan: {', '.join(sorted(unknown))}"
            )


class PolicyError(ValueError):
    """Izin listesi ihlali -> HTTP 400."""


class PermissionError_(PermissionError):
    """Yetki yetersiz -> HTTP 403."""


# KOLONCESI — koleksiyon oncesi takvim (plan A130 + gerceklesen A230 M3'te)
# Kaynak: koleksiyon-oncesi-takvim-widget / M3_TABLO_TASARIMI.md
KOLONCESI = TableSpec(
    name="KOLONCESI",
    pk_fields=("PK01", "PK02", "PK03", "PK04", "PK05", "PK06", "PK07"),
    required_pk=("PK01",),
    writable_fields=frozenset({"A121", "A130", "A230", "A330", "A430", "N096", "N196"}),
    admin_only_fields=frozenset({"A130", "A330", "N096", "N196", "A121"}),
    dept_field="A330",
    description="Koleksiyon oncesi takvim: PK01 sezon, PK02 marka, PK03 alt sezon, "
                "PK04 koleksiyon tipi, PK05 cluster, PK06 model ozellik, PK07 adim anahtari",
)

# CPSTAKVIM — koleksiyon ici plan tarihleri (gerceklesen PLM StyleFollowUp'ta kalir)
CPSTAKVIM = TableSpec(
    name="CPSTAKVIM",
    pk_fields=("PK01", "PK02", "PK03", "PK04"),
    required_pk=("PK01",),
    writable_fields=frozenset({"A121", "A130", "A230", "N096", "N196"}),
    admin_only_write=True,
    description="Koleksiyon ici plan: PK01 sezon, PK02 tedarik (LOCAL/PROD/OVRS/GENEL), "
                "PK03 marka veya ALL, PK04 adim sira",
)

# TEMATAKVIM — tema/in-store tarihine bagli plan tarihleri
TEMATAKVIM = TableSpec(
    name="TEMATAKVIM",
    pk_fields=("PK01", "PK02", "PK03", "PK04"),
    required_pk=("PK01",),
    writable_fields=frozenset({"A121", "A130", "A230", "N096", "N196"}),
    admin_only_write=True,
    description="Tema takvimi: anahtarlama CPSTAKVIM ile ayni, A230 tema (in-store) tarihi",
)

TABLES: dict[str, TableSpec] = {t.name: t for t in (KOLONCESI, CPSTAKVIM, TEMATAKVIM)}

# CUGEX1'in standart alan seti — EXTRA_FILES ile eklenen tablolar icin kullanilir.
GENERIC_PK = tuple(f"PK0{i}" for i in range(1, 9))
GENERIC_WRITABLE = frozenset(
    [f"A{i}30" for i in range(0, 10)]
    + [f"A{i}21" for i in range(0, 10)]
    + [f"N{i}96" for i in range(0, 10)]
    + [f"F1CHB{i}" for i in range(1, 10)]
    + [f"D00{i}" for i in range(1, 10)]
)


def generic_table(name: str) -> TableSpec:
    """EXTRA_FILES ile tanimlanan tablolar icin varsayilan CUGEX1 semasi."""
    return TableSpec(
        name=name.upper(),
        pk_fields=GENERIC_PK,
        required_pk=("PK01",),
        writable_fields=GENERIC_WRITABLE,
        description="EXTRA_FILES ile eklenmis genel CUGEX1 tablosu",
    )


def get_table(name: str, *, extra_files: list[str] | None = None) -> TableSpec:
    key = (name or "").strip().upper()
    spec = TABLES.get(key)
    if spec is None and key and key in {f.upper() for f in (extra_files or [])}:
        spec = generic_table(key)
    if spec is None:
        allowed = sorted(set(TABLES) | {f.upper() for f in (extra_files or [])})
        raise PolicyError(
            f"Izin verilmeyen tablo: {name!r}. Izinli: {', '.join(allowed)}"
        )
    return spec


def check_write_allowed(spec: TableSpec, values: dict[str, str],
                        perms: Permissions, *, dept: str | None) -> None:
    """Yazma yetkisini dogrular. Widget'taki kontrolun sunucu tarafi karsiligi."""
    if perms.is_admin:
        return
    if spec.admin_only_write:
        raise PermissionError_(f"{spec.name} tablosuna yalnizca IT Admin yazabilir.")
    forbidden = sorted(set(values) & spec.admin_only_fields)
    if forbidden:
        raise PermissionError_(
            f"{spec.name}: {', '.join(forbidden)} alanini yalnizca IT Admin degistirebilir."
        )
    if spec.dept_field and not perms.can_edit_dept(dept):
        raise PermissionError_(
            f"'{dept or '-'}' departmani icin duzenleme yetkiniz yok."
        )
