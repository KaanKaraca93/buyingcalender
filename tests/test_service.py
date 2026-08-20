import asyncio

import pytest

from app.ion import M3ApiError, parse_mi_response
from app.policy import get_table
from app.service import list_records, upsert_row, upsert_rows

SPEC = get_table("KOLONCESI")


class FakeM3:
    """CUSEXTMI taklidi: kayitlari bellekte tutar, gercekci hata mesajlari uretir."""

    def __init__(self, existing: set[tuple] | None = None, *, delay: float = 0.0):
        self.existing = set(existing or ())
        self.calls: list[tuple[str, dict]] = []
        self.delay = delay
        # M3'un is-katmani hatasinda dondurdugu durum kodu; testlerde degistirilebilir
        self.error_status = 200

    async def aclose(self) -> None:  # gercek istemciyle ayni arayuz
        return None

    async def execute_raw(self, program, transaction, params=None, *, max_recs=None):
        """Gercek M3 gibi davranir: is-katmani hatasi EXCEPTION DEGIL,
        HTTP 200 govdesinde ErrorMessage olarak doner."""
        try:
            records = await self.execute(program, transaction, params, max_recs=max_recs)
        except M3ApiError as exc:
            if str(exc).startswith("HTTP "):
                raise                      # tasima katmani hatasi -> gercekten firlatilir
            return {"ErrorMessage": str(exc), "ErrorType": "BusinessError"}
        return {"MIRecord": [
            {"NameValue": [{"Name": k, "Value": v} for k, v in rec.items()]}
            for rec in records
        ]}

    async def execute_passthrough(self, program, transaction, params=None, *, max_recs=None):
        """Gercek M3 gibi: is-katmani hatasi govde + durum kodu olarak doner."""
        try:
            records = await self.execute(program, transaction, params, max_recs=max_recs)
        except M3ApiError as exc:
            if str(exc).startswith("HTTP "):
                return 500, {"ErrorMessage": str(exc)}
            return self.error_status, {"ErrorMessage": str(exc), "ErrorType": "BusinessError"}
        return 200, {"MIRecord": [
            {"NameValue": [{"Name": k, "Value": v} for k, v in rec.items()]}
            for rec in records
        ]}

    def _key(self, params: dict) -> tuple:
        return tuple(params.get(f"PK0{i}", "") for i in range(1, 9))

    async def execute(self, program, transaction, params=None, *, max_recs=None):
        params = params or {}
        self.calls.append((transaction, params))
        if self.delay:
            await asyncio.sleep(self.delay)
        key = self._key(params)
        if transaction == "AddFieldValue":
            if key in self.existing:
                raise M3ApiError("CUSEXTMI/AddFieldValue: Record already exists")
            self.existing.add(key)
            return []
        if transaction == "ChgFieldValue":
            if key not in self.existing:
                raise M3ApiError("CUSEXTMI/ChgFieldValue: Record does not exist")
            return []
        if transaction == "LstFieldValue":
            return [{"PK01": params.get("PK01", ""), "A130": "2026-03-10"}]
        raise M3ApiError(f"beklenmeyen transaction: {transaction}")


def keys(step="Makro_Trend_Sunum"):
    return {"PK01": "11", "PK02": "ALL", "PK03": "ALL", "PK04": "ALL",
            "PK05": "ALL", "PK06": "ALL", "PK07": step}


def test_parse_mi_response_maps_namevalue():
    body = {"MIRecord": [{"NameValue": [{"Name": "A130", "Value": "2026-03-10"}]}]}
    assert parse_mi_response(body, "CUSEXTMI", "LstFieldValue") == [{"A130": "2026-03-10"}]


def test_parse_mi_response_raises_on_business_error():
    with pytest.raises(M3ApiError):
        parse_mi_response({"ErrorMessage": "Record does not exist"}, "CUSEXTMI", "ChgFieldValue")


def test_chg_first_existing_record_single_call():
    m3 = FakeM3({tuple(keys().get(f"PK0{i}", "") for i in range(1, 9))})
    action = asyncio.run(upsert_row(m3, SPEC, keys(), {"A230": "2026-03-12"}))
    assert action == "changed"
    assert [c[0] for c in m3.calls] == ["ChgFieldValue"]


def test_chg_first_falls_back_to_add():
    m3 = FakeM3()
    action = asyncio.run(upsert_row(m3, SPEC, keys(), {"A230": "2026-03-12"},
                                    create_values={"A121": "Makro Trend Sunum"}))
    assert action == "added"
    assert [c[0] for c in m3.calls] == ["ChgFieldValue", "AddFieldValue"]
    # create_values + values birlikte yazilmali
    add_params = m3.calls[1][1]
    assert add_params["A121"] == "Makro Trend Sunum"
    assert add_params["A230"] == "2026-03-12"
    assert add_params["FILE"] == "KOLONCESI"


def test_add_first_falls_back_to_chg():
    m3 = FakeM3({tuple(keys().get(f"PK0{i}", "") for i in range(1, 9))})
    action = asyncio.run(upsert_row(m3, SPEC, keys(), {"A230": "2026-03-12"},
                                    strategy="add_first"))
    assert action == "changed"
    assert [c[0] for c in m3.calls] == ["AddFieldValue", "ChgFieldValue"]


def test_unrelated_error_is_not_swallowed():
    class Broken(FakeM3):
        async def execute(self, *a, **k):
            raise M3ApiError("HTTP 500 - CUSEXTMI/ChgFieldValue: gateway patladi")

    with pytest.raises(M3ApiError):
        asyncio.run(upsert_row(Broken(), SPEC, keys(), {"A230": "2026-03-12"}))


def test_upsert_rows_counts_and_isolates_failures():
    class Flaky(FakeM3):
        async def execute(self, program, transaction, params=None, *, max_recs=None):
            if (params or {}).get("PK07") == "bozuk":
                raise M3ApiError("HTTP 500 - patladi")
            return await super().execute(program, transaction, params, max_recs=max_recs)

    rows = [
        {"keys": keys("a"), "values": {"A230": "2026-01-01"}},
        {"keys": keys("bozuk"), "values": {"A230": "2026-01-02"}},
        {"keys": keys("c"), "values": {"A230": "2026-01-03"}},
    ]
    outcome = asyncio.run(upsert_rows(Flaky(), SPEC, rows, concurrency=2))
    assert outcome.added == 2
    assert outcome.failed == 1
    assert outcome.remaining == []
    failed = [r for r in outcome.results if not r.ok][0]
    assert failed.keys["PK07"] == "bozuk"
    # sonuclar satir indeksiyle eslesmeli
    assert sorted(r.index for r in outcome.results) == [0, 1, 2]


def test_deadline_leaves_remaining_rows():
    m3 = FakeM3(delay=0.05)
    rows = [{"keys": keys(f"s{i}"), "values": {"A230": "2026-01-01"}} for i in range(20)]
    outcome = asyncio.run(
        upsert_rows(m3, SPEC, rows, concurrency=1, deadline_seconds=0.12)
    )
    assert outcome.remaining, "sure asimi kalan satirlari bildirmeli"
    assert len(outcome.results) + len(outcome.remaining) == 20


def test_list_records_drops_empty_keys_and_sets_file():
    m3 = FakeM3()
    asyncio.run(list_records(m3, SPEC, {"PK01": "11", "PK02": ""}, max_recs=300))
    tx, params = m3.calls[0]
    assert tx == "LstFieldValue"
    assert params == {"FILE": "KOLONCESI", "PK01": "11"}
