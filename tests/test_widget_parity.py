"""Widget denklik testleri.

Bu dosya, iki widget'in M3'e gonderdigi HER cagriyi birebir tekrar oynatir:

  tenant.ipekyol.koleksiyon.oncesi.takvim  v3.6   -> 3 cagri
      satir 330  LstFieldValue;maxrecs=200  (okuma)
      satir 392  ChgFieldValue              (mevcut satirda tarih guncelleme)
      satir 402  AddFieldValue              (yeni satir olusturma)

  tenant.ipekyol.koleksiyon.ici.takvim     v1.10  -> 2 cagri (ikisi de okuma)
      satir 499  LstFieldValue;maxrecs=300  CPSTAKVIM
      satir 538  LstFieldValue;maxrecs=300  TEMATAKVIM

Amac: proxy'nin M3'e ulastirdigi parametrelerin, widget'in bugun `_qs` ile
urettigi query string ile AYNI olmasi. Fark varsa test kirilir.
"""

import pytest
from fastapi.testclient import TestClient

import tests.test_api  # noqa: F401  (ortam degiskenlerini kurar: AUTH_MODE=dev)
from tests.test_service import FakeM3


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        yield c


def sent(client):
    """FakeM3'e ulasan son (transaction, params) cifti."""
    return client.app.state.m3.calls[-1]


# --------------------------------------------------------------------------- #
# koleksiyon-oncesi-takvim v3.6
# --------------------------------------------------------------------------- #
# _dims(): secilmeyen filtreler "ALL" olur, hicbiri bos gitmez
DIMS = {"PK01": "11", "PK02": "ALL", "PK03": "ALL",
        "PK04": "ALL", "PK05": "ALL", "PK06": "ALL"}


def test_oncesi_read(client):
    """satir 330: LstFieldValue;maxrecs=200?FILE=KOLONCESI&PK01..PK06

    PK07 GONDERILMEZ - tum adimlar enumerate edilsin diye.
    """
    r = client.post("/v1/m3/exec", json={
        "transaction": "LstFieldValue", "maxrecs": 200,
        "params": {"FILE": "KOLONCESI", **DIMS},
    })
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "LstFieldValue"
    assert params == {"FILE": "KOLONCESI", **DIMS}
    assert "MIRecord" in r.json()          # _parseM3 bu govdeyi bekliyor


def test_oncesi_chg_gerceklesen(client):
    """satir 392: ChgFieldValue - gerceklesen tarih + girenin adi (A430)."""
    client.app.state.m3.existing.add(("11", "ALL", "ALL", "ALL", "ALL", "ALL",
                                      "Makro_Trend_Sunum", ""))
    r = client.post("/v1/m3/exec", json={
        "transaction": "ChgFieldValue",
        "params": {"FILE": "KOLONCESI", **DIMS, "PK07": "Makro_Trend_Sunum",
                   "A230": "2026-03-12", "A430": "Muzaffer Kaya"},
    })
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "ChgFieldValue"
    assert params["A230"] == "2026-03-12"
    assert params["A430"] == "Muzaffer Kaya"
    assert params["PK07"] == "Makro_Trend_Sunum"


def test_oncesi_chg_plan_tarihi(client):
    """Plan tarihi (A130) yazma - widget'ta yalnizca IT Admin yapar,
    ama yetki widget'ta kaldigi icin API her rolu gecirmeli."""
    r = client.post("/v1/m3/exec", json={
        "transaction": "ChgFieldValue",
        "params": {"FILE": "KOLONCESI", **DIMS, "PK07": "Tema_Plan_", "A130": "2026-02-01"},
    })
    assert r.status_code == 200, r.text


def test_oncesi_add_sayisal_alanlarla(client):
    """satir 402: AddFieldValue - N096/N196 widget'ta SAYI olarak gider.

    `_qs` bunlari JS'te stringe ceviriyordu; JSON'da sayi olarak gelir.
    Katı dict[str, str] dogrulamasi burada 422 verirdi.
    """
    r = client.post("/v1/m3/exec", json={
        "transaction": "AddFieldValue",
        "params": {
            "FILE": "KOLONCESI", **DIMS, "PK07": "Tema_Plan_",
            "A121": "Tema Plani",
            "A330": "Ürün Yönetimi",
            "N096": 4,                 # <- sayi (row.sira)
            "N196": 1,                 # <- sayi
            "A130": "2026-02-01",
            "A230": "",                # <- bos string (row.gerc yok)
            "A430": "",                # <- bos string
        },
    })
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "AddFieldValue"
    assert params["N096"] == "4" and params["N196"] == "1"      # stringe cevrildi
    assert params["A230"] == "" and params["A430"] == ""        # bos string KORUNDU
    assert params["A330"] == "Ürün Yönetimi"                    # Turkce karakter bozulmadi


def test_oncesi_bos_deger_dusurulmez(client):
    """Bir tarihi temizleme senaryosu: bos string M3'e ulasmali.

    Bos degerler atlanirsa 'sil' islemi sessizce hicbir sey yapmaz.
    """
    client.app.state.m3.existing.add(("11", "ALL", "ALL", "ALL", "ALL", "ALL",
                                      "Range_Plan", ""))
    client.post("/v1/m3/exec", json={
        "transaction": "ChgFieldValue",
        "params": {"FILE": "KOLONCESI", **DIMS, "PK07": "Range_Plan", "A230": ""},
    })
    _, params = sent(client)
    assert "A230" in params and params["A230"] == ""


def test_yazmada_maxrecs_gonderilmez(client):
    """maxrecs yalnizca okuma transaction'inda anlamli."""
    client.app.state.m3.existing.add(("11", "ALL", "ALL", "ALL", "ALL", "ALL", "x", ""))
    client.post("/v1/m3/exec", json={
        "transaction": "ChgFieldValue", "maxrecs": 300,
        "params": {"FILE": "KOLONCESI", **DIMS, "PK07": "x", "A230": "2026-01-01"},
    })
    tx, _ = sent(client)
    assert ";maxrecs" not in tx


# --------------------------------------------------------------------------- #
# koleksiyon-ici-takvim v1.10  (yalnizca okuma)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("supply", ["LOCAL", "PROD", "OVRS", "GENEL"])
def test_ici_cpstakvim_read(client, supply):
    """satir 499: FILE=CPSTAKVIM&PK01=<sezon>&PK02=<tedarik>

    PK03 (marka) BILEREK bos birakilir - marka-spesifik ve ALL kayitlar
    birlikte gelsin diye. Widget bunlari kendi icinde onceliklendiriyor.
    """
    r = client.post("/v1/m3/exec", json={
        "transaction": "LstFieldValue", "maxrecs": 300,
        "params": {"FILE": "CPSTAKVIM", "PK01": "11", "PK02": supply},
    })
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "LstFieldValue"
    assert params == {"FILE": "CPSTAKVIM", "PK01": "11", "PK02": supply}
    assert "PK03" not in params


def test_ici_tematakvim_read(client):
    """satir 538: FILE=TEMATAKVIM, ayni anahtarlama."""
    r = client.post("/v1/m3/exec", json={
        "transaction": "LstFieldValue", "maxrecs": 300,
        "params": {"FILE": "TEMATAKVIM", "PK01": "11", "PK02": "OVRS"},
    })
    assert r.status_code == 200, r.text
    assert sent(client)[1]["FILE"] == "TEMATAKVIM"


def test_ici_widget_m3ye_yazmiyor(client):
    """v1.10'da M3'e yazma yok (gerceklesenler PLM StyleFollowUp'ta kalir).

    Yine de gelecekte gerekirse CPSTAKVIM yazilabilir olmali.
    """
    client.app.state.m3.existing.add(("11", "OVRS", "ALL", "1", "", "", "", ""))
    r = client.post("/v1/m3/exec", json={
        "transaction": "ChgFieldValue",
        "params": {"FILE": "CPSTAKVIM", "PK01": "11", "PK02": "OVRS",
                   "PK03": "ALL", "PK04": "1", "A130": "2026-05-01"},
    })
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Alan kapsami - widget'in yazdigi TUM alanlar izinli mi
# --------------------------------------------------------------------------- #
def test_oncesi_tum_yazilan_alanlar_izinli():
    """Widget'in AddFieldValue'da gonderdigi alanlarin tamami whitelist'te olmali."""
    from app.policy import get_table

    widget_fields = {"A121", "A330", "N096", "N196", "A130", "A230", "A430"}
    spec = get_table("KOLONCESI")
    assert widget_fields <= spec.writable_fields, widget_fields - spec.writable_fields


def test_oncesi_pk_semasi_widget_ile_ayni():
    from app.policy import get_table

    spec = get_table("KOLONCESI")
    assert spec.pk_fields == ("PK01", "PK02", "PK03", "PK04", "PK05", "PK06", "PK07")


def test_ici_pk_semasi_widget_ile_ayni():
    from app.policy import get_table

    for name in ("CPSTAKVIM", "TEMATAKVIM"):
        assert get_table(name).pk_fields == ("PK01", "PK02", "PK03", "PK04")


# --------------------------------------------------------------------------- #
# /v1/m3/x  -  M3 URL'inin birebir taklidi (widget'ta tek satir degisir)
# --------------------------------------------------------------------------- #
def test_passthrough_okuma_m3_ile_ayni(client):
    """Widget'in urettigi URL'in yalnizca oneki degisir."""
    r = client.get("/v1/m3/x/LstFieldValue;maxrecs=300",
                   params={"FILE": "CPSTAKVIM", "PK01": "11", "PK02": "OVRS"})
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "LstFieldValue"
    assert params == {"FILE": "CPSTAKVIM", "PK01": "11", "PK02": "OVRS"}
    assert "MIRecord" in r.json()


def test_passthrough_yazma(client):
    client.app.state.m3.existing.add(("11", "ALL", "ALL", "ALL", "ALL", "ALL",
                                      "Makro_Trend_Sunum", ""))
    r = client.get("/v1/m3/x/ChgFieldValue", params={
        "FILE": "KOLONCESI", **DIMS, "PK07": "Makro_Trend_Sunum",
        "A230": "2026-03-12", "A430": "Muzaffer Kaya",
    })
    assert r.status_code == 200, r.text
    tx, params = sent(client)
    assert tx == "ChgFieldValue"
    assert params["A430"] == "Muzaffer Kaya"


def test_passthrough_bos_deger_korunur(client):
    client.app.state.m3.existing.add(("11", "ALL", "ALL", "ALL", "ALL", "ALL", "R", ""))
    client.get("/v1/m3/x/ChgFieldValue",
               params={"FILE": "KOLONCESI", **DIMS, "PK07": "R", "A230": ""})
    assert sent(client)[1]["A230"] == ""


def test_passthrough_izin_listesi_gecerli(client):
    assert client.get("/v1/m3/x/LstFieldValue",
                      params={"FILE": "OCUSMA", "PK01": "1"}).status_code == 400
    assert client.get("/v1/m3/x/DelFieldValue",
                      params={"FILE": "KOLONCESI", **DIMS, "PK07": "x"}).status_code == 400
    assert client.get("/v1/m3/x/ChgFieldValue",
                      params={"FILE": "KOLONCESI", **DIMS, "PK07": "x",
                              "A930": "hack"}).status_code == 400


def test_passthrough_is_katmani_hatasi_200_doner(client):
    """M3 'kayit yok' hatasini 200 + ErrorMessage olarak doner; widget'in
    hata yonetimi bugun buna gore yazilmis, ayni kalmali."""
    r = client.get("/v1/m3/x/ChgFieldValue",
                   params={"FILE": "KOLONCESI", **DIMS, "PK07": "olmayan",
                           "A230": "2026-01-01"})
    assert r.status_code == 200
    assert "ErrorMessage" in r.json()
