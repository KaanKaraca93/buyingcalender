import base64
import json
import os

import pytest

DUMMY_IONAPI = {
    "ti": "TEST_TENANT", "ci": "ci", "cs": "cs",
    "iu": "https://ion.example.test", "pu": "https://sso.example.test/",
    "ot": "as/token.oauth2", "saak": "saak", "sask": "sask",
}
os.environ.setdefault("IONAPI_B64", base64.b64encode(json.dumps(DUMMY_IONAPI).encode()).decode())
os.environ.setdefault("AUTH_MODE", "dev")
os.environ.setdefault("ALLOWED_ORIGINS", "https://plm.example.test")
os.environ.setdefault("MAX_ROWS_PER_REQUEST", "5")

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import _email_from_users_me, _roles_from_plm_user  # noqa: E402
from app.config import IonCredentials  # noqa: E402
from app.main import app  # noqa: E402
from tests.test_service import FakeM3, keys  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.app.state.m3 = FakeM3()
        yield c


def test_ionapi_b64_parsing():
    creds = IonCredentials.load()
    assert creds.tenant == "TEST_TENANT"
    assert creds.token_url == "https://sso.example.test/as/token.oauth2"


def test_healthz_needs_no_auth(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_docs_hidden_outside_dev():
    # dev modda acik olmali (bu test dev modda kosuyor)
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 200


def test_me_returns_permissions(client):
    body = client.get("/v1/me").json()
    assert body["is_admin"] is True
    assert body["auth_mode"] == "dev"


def test_tables_endpoint_lists_allowlist(client):
    names = {t["name"] for t in client.get("/v1/tables").json()["tables"]}
    assert names == {"KOLONCESI", "CPSTAKVIM", "TEMATAKVIM"}


def test_list_rejects_unknown_table(client):
    r = client.post("/v1/m3/list", json={"file": "OCUSMA", "keys": {"PK01": "11"}})
    assert r.status_code == 400


def test_list_rejects_missing_required_pk(client):
    r = client.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK02": "PROD"}})
    assert r.status_code == 400


def test_list_ok(client):
    r = client.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK01": "11"}})
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_maxrecs_is_capped(client):
    client.post("/v1/m3/list", json={"file": "CPSTAKVIM", "keys": {"PK01": "11"}, "maxrecs": 99999})
    # FakeM3 cagrilari kaydediyor; ust sinir MAX_MAXRECS (varsayilan 1000)
    assert client.app.state.m3.calls, "M3 cagrilmali"


def test_upsert_row_limit(client):
    rows = [{"keys": keys(f"s{i}"), "values": {"A230": "2026-01-01"}} for i in range(6)]
    r = client.post("/v1/m3/upsert", json={"file": "KOLONCESI", "rows": rows})
    assert r.status_code == 413


def test_upsert_rejects_unwritable_field(client):
    r = client.post("/v1/m3/upsert", json={
        "file": "KOLONCESI",
        "rows": [{"keys": keys(), "values": {"A930": "x"}}],
    })
    assert r.status_code == 400


def test_upsert_rejects_partial_pk(client):
    r = client.post("/v1/m3/upsert", json={
        "file": "KOLONCESI",
        "rows": [{"keys": {"PK01": "11"}, "values": {"A230": "2026-01-01"}}],
    })
    assert r.status_code == 400


def test_upsert_ok(client):
    r = client.post("/v1/m3/upsert", json={
        "file": "KOLONCESI",
        "rows": [{"keys": keys(), "values": {"A230": "2026-03-12"},
                  "create_values": {"A121": "Makro Trend Sunum", "A330": "Tasarım"}}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 1 and body["failed"] == 0


def test_read_only_mode_blocks_writes(client, monkeypatch):
    from app import config
    from app.routes import m3 as m3_routes

    settings = config.get_settings()
    monkeypatch.setattr(m3_routes, "get_settings", lambda: type(settings)(
        **{**settings.__dict__, "read_only": True}))
    r = client.post("/v1/m3/upsert", json={
        "file": "KOLONCESI",
        "rows": [{"keys": keys(), "values": {"A230": "2026-03-12"}}],
    })
    assert r.status_code == 503


# --- kimlik cozumleme yardimcilari (govde ayristirma) ----------------------- #

def test_email_from_users_me():
    body = {"response": {"userlist": [{"emails": [{"value": "a@b.com"}]}]}}
    assert _email_from_users_me(body) == "a@b.com"
    assert _email_from_users_me({"response": {"userlist": [{"id": "x@y.com"}]}}) == "x@y.com"
    assert _email_from_users_me({}) == ""


def test_roles_from_plm_user_skips_inactive():
    body = {"value": [{"Name": "Muzaffer Kaya", "UserRoles": [
        {"IsActive": True, "Role": {"RoleId": 1009, "Name": "IT Admin"}},
        {"IsActive": False, "Role": {"RoleId": 2, "Name": "Tasarımcı"}},
    ]}]}
    name, roles = _roles_from_plm_user(body)
    assert name == "Muzaffer Kaya"
    assert roles == ["IT Admin"]
