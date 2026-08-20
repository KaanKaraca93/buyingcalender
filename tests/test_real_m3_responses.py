"""GERCEK M3 yanitlariyla dogrulama.

Buradaki govdeler 20.08.2026'da TST ortamindan HAR ve Postman ile birebir
kaydedildi. Uydurma degil - kopyala yapistir.

En onemli bulgu: M3 is-katmani hatasini **HTTP 200** icinde donuyor ve alan adi
`ErrorMessage` degil **`Message`**.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import tests.test_api  # noqa: F401
from app.ion import M3ApiError, m3_error_of, parse_mi_response
from app.policy import get_table
from app.service import upsert_row
from tests.test_service import FakeM3

# --------------------------------------------------------------------------- #
# HAR / Postman'dan alinan GERCEK govdeler
# --------------------------------------------------------------------------- #
ADD_OK = {"Program": "CUSEXTMI", "Transaction": "AddFieldValue",
          "Metadata": {"Field": []},
          "MIRecord": [{"NameValue": [], "RowIndex": 0}]}

CHG_OK = {"Program": "CUSEXTMI", "Transaction": "ChgFieldValue",
          "Metadata": {"Field": []},
          "MIRecord": [{"NameValue": [], "RowIndex": 0}]}

NOT_FOUND = {"@type": "ServerReturnedNOK", "@code": "XRE0103", "@cfg": "  ",
             "@field": "", "Message": "Record does not exist"}

ALREADY_EXISTS = {"@type": "ServerReturnedNOK", "@code": "XRE0104", "@cfg": "  ",
                  "@field": "", "Message": "The record already exists"}

LST_OK = {"Program": "CUSEXTMI", "Transaction": "LstFieldValue",
          "Metadata": {"Field": []},
          "MIRecord": [{"RowIndex": 0, "NameValue": [
              {"Name": "FILE", "Value": "CPSTAKVIM"},
              {"Name": "PK01", "Value": "11"},
              {"Name": "PK02", "Value": "OVRS"},
              {"Name": "PK03", "Value": "8"},
              {"Name": "PK04", "Value": "01"},
              {"Name": "A130", "Value": "2026-04-22"},
              {"Name": "A230", "Value": "Dosya Hazırlık"},
              {"Name": "N096", "Value": "1.000000"},
              {"Name": "N196", "Value": "2.000000"},
              {"Name": "CHID", "Value": "MKAYA"},
              {"Name": "A121", "Value": "Spec Dosya Hazırlanması"},
          ]}]}


# --------------------------------------------------------------------------- #
# Hata tanima
# --------------------------------------------------------------------------- #
def test_basarili_yanitlar_hata_sayilmaz():
    for body in (ADD_OK, CHG_OK, LST_OK):
        assert m3_error_of(body) is None


def test_gercek_hata_zarfi_taninir():
    assert m3_error_of(NOT_FOUND) == ("XRE0103", "Record does not exist")
    assert m3_error_of(ALREADY_EXISTS) == ("XRE0104", "The record already exists")


def test_message_alani_errormessage_degil():
    """Kod `ErrorMessage` ariyor olsaydi bu hatalari hic goremezdik."""
    assert "ErrorMessage" not in NOT_FOUND
    assert "Message" in NOT_FOUND
    assert m3_error_of(NOT_FOUND) is not None


def test_parse_mi_response_gercek_hatada_firlatir():
    with pytest.raises(M3ApiError) as e:
        parse_mi_response(NOT_FOUND, "CUSEXTMI", "ChgFieldValue")
    assert e.value.code == "XRE0103"


def test_lst_yaniti_dogru_ayrisir():
    recs = parse_mi_response(LST_OK, "CUSEXTMI", "LstFieldValue")
    assert len(recs) == 1
    r = recs[0]
    assert r["A121"] == "Spec Dosya Hazırlanması"
    assert r["N096"] == "1.000000"       # 6 ondalik - widget parseInt ile okuyor
    assert r["PK03"] == "8"              # marka bazli kayit (ALL degil!)


def test_yazma_basarisi_bos_mirecord_dondurur():
    assert parse_mi_response(ADD_OK, "CUSEXTMI", "AddFieldValue") == [{}]


# --------------------------------------------------------------------------- #
# Upsert fallback - artik HATA KODU ile eslesiyor
# --------------------------------------------------------------------------- #
class RealM3(FakeM3):
    """Gercek M3 gibi: is hatasi HTTP 200 govdesinde, kod XRE01xx."""

    async def execute(self, program, transaction, params=None, *, max_recs=None):
        self.calls.append((transaction, params or {}))
        key = self._key(params or {})
        if transaction == "AddFieldValue":
            if key in self.existing:
                return parse_mi_response(ALREADY_EXISTS, program, transaction)
            self.existing.add(key)
            return parse_mi_response(ADD_OK, program, transaction)
        if transaction == "ChgFieldValue":
            if key not in self.existing:
                return parse_mi_response(NOT_FOUND, program, transaction)
            return parse_mi_response(CHG_OK, program, transaction)
        return parse_mi_response(LST_OK, program, transaction)


SPEC = get_table("CPSTAKVIM")
KEYS = {"PK01": "11", "PK02": "OVRS", "PK03": "ALL", "PK04": "01"}


def test_chg_first_gercek_hata_koduyla_add_e_duser():
    m3 = RealM3()
    action = asyncio.run(upsert_row(m3, SPEC, KEYS, {"A130": "2026-04-25"},
                                    create_values={"A121": "Spec Dosya"}))
    assert action == "added"
    assert [c[0] for c in m3.calls] == ["ChgFieldValue", "AddFieldValue"]


def test_add_first_gercek_hata_koduyla_chg_e_duser():
    m3 = RealM3({tuple(KEYS.get(f"PK0{i}", "") for i in range(1, 9))})
    action = asyncio.run(upsert_row(m3, SPEC, KEYS, {"A130": "2026-04-25"},
                                    strategy="add_first"))
    assert action == "changed"
    assert [c[0] for c in m3.calls] == ["AddFieldValue", "ChgFieldValue"]


# --------------------------------------------------------------------------- #
# Passthrough - sessiz hata gorunur olmali
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        yield c


class ErrorM3(FakeM3):
    async def execute_passthrough(self, *a, **k):
        return 200, NOT_FOUND


def test_sessiz_hata_govdesi_aynen_gecer_ama_baslikta_gorunur(client):
    """Widget'in gordugu sey degismez (200 + ayni govde);
    ama X-M3-Error basligi ve denetim kaydi sessiz basarisizligi ele verir."""
    client.app.state.m3 = ErrorM3()
    r = client.get("/v1/m3/x/ChgFieldValue",
                   params={"FILE": "CPSTAKVIM", **KEYS, "A130": "2026-01-01"})
    assert r.status_code == 200
    assert r.json() == NOT_FOUND
    assert r.headers.get("X-M3-Error-Code") == "XRE0103"
    assert "does not exist" in r.headers.get("X-M3-Error", "")


def test_basarili_yanitta_hata_basligi_yok(client):
    r = client.get("/v1/m3/x/LstFieldValue;maxrecs=300",
                   params={"FILE": "CPSTAKVIM", "PK01": "11", "PK02": "OVRS"})
    assert r.status_code == 200
    assert "X-M3-Error-Code" not in r.headers


# --------------------------------------------------------------------------- #
# Gercek widget parametreleri (HAR'dan)
# --------------------------------------------------------------------------- #
def test_oncesi_gercek_dims_ile_okuma(client):
    """HAR: FILE=KOLONCESI&PK01=5&PK02=23&PK03=FW4&PK04=001&PK05=013&PK06=1
    Dikkat: dims 'ALL' degil, gercek kod degerleri."""
    real = {"PK01": "5", "PK02": "23", "PK03": "FW4",
            "PK04": "001", "PK05": "013", "PK06": "1"}
    r = client.get("/v1/m3/x/LstFieldValue;maxrecs=200",
                   params={"FILE": "KOLONCESI", **real})
    assert r.status_code == 200
    _, params = client.app.state.m3.calls[-1]
    assert params == {"FILE": "KOLONCESI", **real}


def test_oncesi_gercek_add_payload(client):
    """HAR'daki AddFieldValue parametrelerinin birebir aynisi."""
    r = client.get("/v1/m3/x/AddFieldValue", params={
        "FILE": "KOLONCESI", "PK01": "5", "PK02": "23", "PK03": "FW4",
        "PK04": "001", "PK05": "013", "PK06": "1", "PK07": "Makro_Trend_Sunum",
        "A121": "Makro Trend Sunum", "A330": "Tasarım",
        "N096": "1", "N196": "1", "A130": "2026-08-20", "A230": "", "A430": "",
    })
    assert r.status_code == 200, r.text
    _, params = client.app.state.m3.calls[-1]
    assert params["A330"] == "Tasarım"
    assert params["A230"] == "" and params["A430"] == ""
