"""Config var cozumleme — Heroku'da hangi isimlerle girilirse calisir."""

import base64
import json

import pytest

from app import config
from app.config import ConfigError, IonCredentials

IONAPI = {
    "ti": "ATJZAMEWEF5P4SNV_TST",
    "ci": "ATJZAMEWEF5P4SNV_TST~abc",
    "cs": "gizli",
    "iu": "https://mingle-ionapi.eu1.inforcloudsuite.com",
    "pu": "https://mingle-sso.eu1.inforcloudsuite.com:443/ATJZAMEWEF5P4SNV_TST/as/",
    "ot": "token.oauth2",
    "saak": "ATJZAMEWEF5P4SNV_TST#key",
    "sask": "sirr",
}
SSO = "https://mingle-sso.eu1.inforcloudsuite.com/ATJZAMEWEF5P4SNV_TST/as/token.oauth2"

ALL_KEYS = ["IONAPI_B64", "IONAPI_JSON", "IONAPI_FILE", "ION_TENANT",
            "ci", "cs", "saak", "sask", "sso", "ion", "ti"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    config.get_ion_credentials.cache_clear()
    yield
    config.get_ion_credentials.cache_clear()


def test_ionapi_b64(monkeypatch):
    monkeypatch.setenv("IONAPI_B64", base64.b64encode(json.dumps(IONAPI).encode()).decode())
    c = IonCredentials.load()
    assert c.tenant == "ATJZAMEWEF5P4SNV_TST"
    assert c.token_url.endswith("/as/token.oauth2")


def test_short_names_like_heroku_panel(monkeypatch):
    """ci / cs / saak / sask / sso — panelde tek tek girilen isimler."""
    monkeypatch.setenv("ci", IONAPI["ci"])
    monkeypatch.setenv("cs", IONAPI["cs"])
    monkeypatch.setenv("saak", IONAPI["saak"])
    monkeypatch.setenv("sask", IONAPI["sask"])
    monkeypatch.setenv("sso", SSO)
    c = IonCredentials.load()
    assert c.tenant == "ATJZAMEWEF5P4SNV_TST"        # ci'nin '~' oncesinden turetilir
    assert c.token_url == SSO
    assert c.ion_url == "https://mingle-ionapi.eu1.inforcloudsuite.com"  # sso'dan turetilir
    assert c.saak == IONAPI["saak"]


def test_short_names_explicit_ion_and_ti_win(monkeypatch):
    monkeypatch.setenv("ci", "bozuk-format")
    monkeypatch.setenv("cs", "x")
    monkeypatch.setenv("saak", "y")
    monkeypatch.setenv("sask", "z")
    monkeypatch.setenv("sso", SSO)
    monkeypatch.setenv("ti", "BASKA_TENANT")
    monkeypatch.setenv("ion", "https://ozel.example.com/")
    c = IonCredentials.load()
    assert c.tenant == "BASKA_TENANT"
    assert c.ion_url == "https://ozel.example.com"


def test_short_names_tenant_from_sso_when_ci_has_no_tilde(monkeypatch):
    monkeypatch.setenv("ci", "tildesiz")
    monkeypatch.setenv("cs", "x")
    monkeypatch.setenv("saak", "y")
    monkeypatch.setenv("sask", "z")
    monkeypatch.setenv("sso", SSO)
    assert IonCredentials.load().tenant == "ATJZAMEWEF5P4SNV_TST"


def test_short_names_without_sso_is_clear_error(monkeypatch):
    monkeypatch.setenv("ci", IONAPI["ci"])
    monkeypatch.setenv("saak", IONAPI["saak"])
    with pytest.raises(ConfigError, match="sso"):
        IonCredentials.load()


def test_no_credentials_error_lists_all_options(monkeypatch):
    with pytest.raises(ConfigError) as exc:
        IonCredentials.load()
    msg = str(exc.value)
    assert "IONAPI_B64" in msg and "saak" in msg and "ION_TENANT" in msg


def test_client_does_not_resolve_credentials_at_construction(monkeypatch):
    """Yanlis config'de bile uygulama ayaga kalkmali (dyno cokmesin)."""
    from app.ion import IonServiceClient

    client = IonServiceClient()          # hicbir credential yok -> patlamamali
    with pytest.raises(ConfigError):
        _ = client._cfg                  # ilk kullanimda yuzeye cikar
