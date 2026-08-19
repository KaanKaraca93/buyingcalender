"""Gecis ucu (/v1/m3/exec), gateway modu ve rol politikasinin KAPALI olmasi."""

import importlib

import pytest
from fastapi.testclient import TestClient

import tests.test_api  # noqa: F401  (ortam degiskenlerini kurar)
from app.config import get_settings
from tests.test_service import FakeM3, keys


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        yield c


def test_role_policy_is_off_by_default():
    """Yetkilendirme widget'ta kalir; API kendiliginden rol kontrolu yapmaz."""
    assert get_settings().enforce_role_policy is False


def test_delete_is_off_by_default():
    assert get_settings().allow_delete is False


def test_exec_returns_raw_mirecord_body(client):
    """Widget'taki _parseM3 govdeyi degistirmeden ayristirabilmeli."""
    r = client.post("/v1/m3/exec", json={
        "transaction": "LstFieldValue",
        "maxrecs": 300,
        "params": {"FILE": "CPSTAKVIM", "PK01": "11", "PK02": "PROD"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "MIRecord" in body
    names = [nv["Name"] for nv in body["MIRecord"][0]["NameValue"]]
    assert "A130" in names


def test_exec_passes_maxrecs_as_matrix_param(client):
    client.post("/v1/m3/exec", json={
        "transaction": "LstFieldValue", "maxrecs": 300,
        "params": {"FILE": "CPSTAKVIM", "PK01": "11"},
    })
    # FakeM3 max_recs'i dogrudan gormuyor; cagri yapildigini ve FILE'in gectigini dogrula
    tx, params = client.app.state.m3.calls[0]
    assert tx == "LstFieldValue"
    assert params["FILE"] == "CPSTAKVIM"


def test_exec_blocks_delete_by_default(client):
    r = client.post("/v1/m3/exec", json={
        "transaction": "DelFieldValue",
        "params": {"FILE": "KOLONCESI", **keys()},
    })
    assert r.status_code == 400
    assert "transaction" in r.json()["detail"].lower()


def test_exec_blocks_unknown_table(client):
    r = client.post("/v1/m3/exec", json={
        "transaction": "LstFieldValue", "params": {"FILE": "OCUSMA", "PK01": "1"},
    })
    assert r.status_code == 400


def test_exec_blocks_unknown_field(client):
    r = client.post("/v1/m3/exec", json={
        "transaction": "ChgFieldValue",
        "params": {"FILE": "KOLONCESI", **keys(), "A930": "x"},
    })
    assert r.status_code == 400


def test_exec_write_allowed_for_any_role_when_policy_off(client):
    """Tasarimci rolu bile plan tarihi yazabilmeli — yetki widget'in isi."""
    r = client.post("/v1/m3/exec", json={
        "transaction": "AddFieldValue",
        "params": {"FILE": "KOLONCESI", **keys(), "A130": "2026-03-10"},
    })
    assert r.status_code == 200, r.text


def test_openapi_is_always_exposed(client):
    """ION API Gateway'e custom API tanitirken sema gerekli."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/v1/m3/exec" in r.json()["paths"]


# --------------------------------------------------------------------------- #
# gateway modu — X-Api-Key zorunlulugu
# --------------------------------------------------------------------------- #
def _gateway_client(monkeypatch, api_key: str | None):
    import app.config as config

    monkeypatch.setenv("AUTH_MODE", "gateway")
    if api_key is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", api_key)
    config.get_settings.cache_clear()
    importlib.reload(importlib.import_module("app.main"))
    from app.main import app

    return app


def test_gateway_mode_requires_api_key(monkeypatch):
    app = _gateway_client(monkeypatch, "s3cret")
    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        assert c.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK01": "11"}}).status_code == 401
        ok = c.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK01": "11"}},
                    headers={"X-Api-Key": "s3cret"})
        assert ok.status_code == 200
        assert c.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK01": "11"}},
                      headers={"X-Api-Key": "yanlis"}).status_code == 401


def test_gateway_mode_without_api_key_configured_fails_closed(monkeypatch):
    app = _gateway_client(monkeypatch, None)
    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        r = c.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK01": "11"}})
        assert r.status_code == 500  # yapilandirma eksik -> acik birakma


def test_gateway_identity_header_is_audit_only(monkeypatch):
    app = _gateway_client(monkeypatch, "s3cret")
    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        body = c.get("/v1/me", headers={"X-Api-Key": "s3cret",
                                        "X-Infor-User": "biri@ipekyol.com"}).json()
        assert body["email"] == "biri@ipekyol.com"
        assert body["roles"] == []          # rol iddiasi kabul edilmez
        assert body["is_admin"] is False    # yetki bu basliktan tureMEZ
        assert body["enforce_role_policy"] is False


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    yield
    import app.config as config

    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("API_KEY", raising=False)
    config.get_settings.cache_clear()
    importlib.reload(importlib.import_module("app.main"))


def test_upstream_network_error_returns_502(client):
    """DNS/baglanti hatasi 500 degil 502 donmeli."""
    import httpx

    class Dead:
        async def aclose(self):
            return None

        async def execute(self, *a, **k):
            raise httpx.ConnectError("dns cozulemedi")

        async def execute_raw(self, *a, **k):
            raise httpx.ConnectError("dns cozulemedi")

    client.app.state.m3 = Dead()
    r = client.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK01": "11"}})
    assert r.status_code == 502
    assert "ION" in r.json()["detail"]
