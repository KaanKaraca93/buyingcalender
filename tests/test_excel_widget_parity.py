"""tenant.ipekyol.m3.takvim.excel v1.4 denklik testleri.

Widget'in M3'e giden cagrilari (kaynak satirlariyla):
    358  LstFieldValue;maxrecs=300?FILE=<CPSTAKVIM|TEMATAKVIM>&PK01=<sezon>&PK02=<tedarik>
    387  ChgFieldValue   (kayit state map'te varsa dogrudan)
    388  AddFieldValue   (yoksa once bu)
    393  ChgFieldValue   (Add "already exist" hatasi verirse fallback)

Kritik davranis: fallback, HATA GOVDESINDEKI "already exist" metnine bakiyor
(`extractM3ErrorMessage` -> body.ErrorMessage / body.Message). Proxy hatayi
kendi formatina sararsa bu mantik sessizce bozulur -> her satir "hata" olur.
"""

import pytest
from fastapi.testclient import TestClient

import tests.test_api  # noqa: F401
from tests.test_service import FakeM3

SUPPLY_ACTTYPE = {"LOCAL": 1, "OVRS": 2, "PROD": 3, "GENEL": 4}
TEMA_ACTTYPE = 5


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        yield c


def sent(client):
    return client.app.state.m3.calls[-1]


def cps_params(pk04="01", supply="OVRS", version=1):
    """satir 750: widget'in urettigi parametre seti (N096/N196 SAYI)."""
    return {
        "PK01": "11", "PK02": supply, "PK03": "ALL", "PK04": pk04,
        "A121": "Spec Dosya Hazirlanmasi", "A130": "2026-05-01",
        "A230": "Numune",
        "N096": version, "N196": SUPPLY_ACTTYPE[supply],
    }


# --------------------------------------------------------------------------- #
# Okuma
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("file", ["CPSTAKVIM", "TEMATAKVIM"])
@pytest.mark.parametrize("supply", ["LOCAL", "OVRS", "PROD", "GENEL"])
def test_excel_read(client, file, supply):
    r = client.get(f"/v1/m3/x/LstFieldValue;maxrecs=300",
                   params={"FILE": file, "PK01": "11", "PK02": supply})
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "LstFieldValue"
    assert params == {"FILE": file, "PK01": "11", "PK02": supply}


# --------------------------------------------------------------------------- #
# Yazma
# --------------------------------------------------------------------------- #
def test_excel_add_sayisal_alanlar(client):
    """N096 (versiyon) ve N196 (ActType) sayi olarak gonderiliyor."""
    r = client.get("/v1/m3/x/AddFieldValue",
                   params={"FILE": "CPSTAKVIM", **cps_params()})
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "AddFieldValue"
    assert params["N096"] == "1" and params["N196"] == "2"
    assert params["PK03"] == "ALL"


def test_excel_tema_yazma(client):
    """TEMATAKVIM: A230 tema (in-store) tarihi, N196 = 5."""
    r = client.get("/v1/m3/x/AddFieldValue", params={
        "FILE": "TEMATAKVIM", "PK01": "11", "PK02": "OVRS", "PK03": "ALL", "PK04": "0101",
        "A121": "Prova Onay Tarihi", "A130": "2026-04-15", "A230": "2026-09-01",
        "N096": 1, "N196": TEMA_ACTTYPE,
    })
    assert r.status_code == 200, r.text
    _, params = sent(client)
    assert params["FILE"] == "TEMATAKVIM" and params["N196"] == "5"


def test_excel_chg_mevcut_kayit(client):
    """isExisting=true -> dogrudan ChgFieldValue."""
    client.app.state.m3.existing.add(("11", "OVRS", "ALL", "01", "", "", "", ""))
    r = client.get("/v1/m3/x/ChgFieldValue",
                   params={"FILE": "CPSTAKVIM", **cps_params(version=2)})
    assert r.status_code == 200, r.text
    assert sent(client)[0] == "ChgFieldValue"


@pytest.mark.parametrize("m3_status", [200, 400, 500])
def test_excel_already_exist_govdesi_aynen_gecer(client, m3_status):
    """EN KRITIK TEST.

    Add ayni kayda ikinci kez gelirse M3 "already exist" der. Widget bu metni
    hata govdesinden okuyup ChgFieldValue'ya duser. Proxy hem durum kodunu hem
    govdeyi aynen aktarmali; aksi halde `body.ErrorMessage` kaybolur ve
    fallback hic calismaz.
    """
    client.app.state.m3.error_status = m3_status
    p = {"FILE": "CPSTAKVIM", **cps_params()}

    first = client.get("/v1/m3/x/AddFieldValue", params=p)
    assert first.status_code == 200          # ilk ekleme basarili

    second = client.get("/v1/m3/x/AddFieldValue", params=p)
    assert second.status_code == m3_status, "M3'un durum kodu aynen aktarilmali"
    body = second.json()
    assert "ErrorMessage" in body, "govde sarmalanmamali - widget bu alani okuyor"
    assert "already exist" in body["ErrorMessage"].lower()

    # widget'in fallback'i: ayni PK ile ChgFieldValue
    third = client.get("/v1/m3/x/ChgFieldValue", params=p)
    assert third.status_code == 200


def test_excel_bos_deger_gondermez_ama_gecerse_korunur(client):
    """Widget bos degerleri kendisi filtreliyor (satir 378); yine de gecerse
    proxy dusurmemeli."""
    client.app.state.m3.existing.add(("11", "OVRS", "ALL", "01", "", "", "", ""))
    client.get("/v1/m3/x/ChgFieldValue",
               params={"FILE": "CPSTAKVIM", **cps_params(), "A230": ""})
    assert sent(client)[1]["A230"] == ""


# --------------------------------------------------------------------------- #
# Kapsam
# --------------------------------------------------------------------------- #
def test_excel_yazdigi_alanlar_izinli():
    from app.policy import get_table

    fields = {"A121", "A130", "A230", "N096", "N196"}
    for name in ("CPSTAKVIM", "TEMATAKVIM"):
        spec = get_table(name)
        assert fields <= spec.writable_fields, name


def test_excel_pk04_dort_haneli_tema_sirasi(client):
    """Tema kayitlarinda PK04 = tidx + sira (4 hane). PK semasi bunu kaldirmali."""
    client.get("/v1/m3/x/AddFieldValue", params={
        "FILE": "TEMATAKVIM", "PK01": "11", "PK02": "PROD", "PK03": "ALL",
        "PK04": "0312", "A121": "Maliyet Onayi", "A130": "2026-04-01",
        "A230": "2026-09-01", "N096": 1, "N196": 5,
    })
    assert sent(client)[1]["PK04"] == "0312"
