import pytest

from app.policy import (
    PermissionError_,
    PolicyError,
    check_write_allowed,
    get_table,
    norm_role,
    permissions_for_roles,
)


def test_unknown_table_rejected():
    with pytest.raises(PolicyError):
        get_table("OCUSMA")
    with pytest.raises(PolicyError):
        get_table("")


def test_known_tables():
    assert get_table("koloncesi").name == "KOLONCESI"
    assert get_table(" cpstakvim ").name == "CPSTAKVIM"
    assert get_table("TEMATAKVIM").pk_fields == ("PK01", "PK02", "PK03", "PK04")


def test_norm_role_turkish():
    assert norm_role("Kumaş Tasarım") == "kumas tasarim"
    assert norm_role("  IT   Admin ") == "it admin"


def test_permissions_admin():
    perms = permissions_for_roles(["IT Admin"])
    assert perms.is_admin
    assert perms.can_edit_dept("Sourcing")


def test_permissions_department_scoped():
    perms = permissions_for_roles(["Sourcing Uzmanı"])
    assert not perms.is_admin
    assert perms.can_edit_dept("Sourcing")
    assert not perms.can_edit_dept("Tasarım")


def test_permissions_kumas_before_tasarim():
    # "Kumaş Tasarım" rolu Tasarımcı kuralina dusmemeli
    perms = permissions_for_roles(["Kumaş Tedarik"])
    assert perms.editable_depts == frozenset({"Kumaş Tasarım"})


def test_permissions_no_role_is_read_only():
    perms = permissions_for_roles([])
    assert not perms.is_admin
    assert not perms.can_edit_dept("Tasarım")


def test_unknown_pk_rejected():
    spec = get_table("KOLONCESI")
    with pytest.raises(PolicyError):
        spec.validate_keys({"PK09": "x"}, for_write=False)


def test_write_requires_all_pks():
    spec = get_table("KOLONCESI")
    with pytest.raises(PolicyError):
        spec.validate_keys({"PK01": "11"}, for_write=True)
    spec.validate_keys(
        {f"PK0{i}": "ALL" for i in range(1, 8)}, for_write=True
    )


def test_read_requires_pk01():
    spec = get_table("CPSTAKVIM")
    with pytest.raises(PolicyError):
        spec.validate_keys({"PK02": "PROD"}, for_write=False)
    spec.validate_keys({"PK01": "11", "PK02": "PROD"}, for_write=False)


def test_non_writable_field_rejected():
    spec = get_table("KOLONCESI")
    with pytest.raises(PolicyError):
        spec.validate_values({"A930": "hack"})
    spec.validate_values({"A230": "2026-03-12"})


def test_plan_date_is_admin_only():
    spec = get_table("KOLONCESI")
    designer = permissions_for_roles(["Tasarımcı"])
    with pytest.raises(PermissionError_):
        check_write_allowed(spec, {"A130": "2026-03-10"}, designer, dept="Tasarım")
    # gerceklesen kendi departmaninda serbest
    check_write_allowed(spec, {"A230": "2026-03-12"}, designer, dept="Tasarım")


def test_other_department_rejected():
    spec = get_table("KOLONCESI")
    designer = permissions_for_roles(["Tasarımcı"])
    with pytest.raises(PermissionError_):
        check_write_allowed(spec, {"A230": "2026-03-12"}, designer, dept="Sourcing")


def test_cpstakvim_write_admin_only():
    spec = get_table("CPSTAKVIM")
    designer = permissions_for_roles(["Tasarımcı"])
    with pytest.raises(PermissionError_):
        check_write_allowed(spec, {"A130": "2026-05-01"}, designer, dept=None)
    check_write_allowed(spec, {"A130": "2026-05-01"}, permissions_for_roles(["IT Admin"]), dept=None)
