import pytest

from smara.plugins import manifests


def test_builtin_plugin_catalog_is_declarative():
    values = manifests()
    assert any(item["name"] == "smara-desktop" and item["approval_required"] for item in values)


def test_local_only_plugin_catalog_hides_user_integrations():
    assert all(item["name"] != "smara-integrations" for item in manifests(include_user_integrations=False))


def test_external_plugin_metadata_is_bounded_and_opt_in():
    values = manifests('[{"name":"docs","kind":"mcp","version":"1","enabled":false,"tools":["search"]}]')
    assert values[-1]["enabled"] is False
    with pytest.raises(ValueError):
        manifests('[{"name":"Bad Name","kind":"mcp","tools":[]}]')
