import copy

import pytest

from musebridge.tool_surface import (
    CORE_TOOLS,
    DEFAULT_PACKS,
    PACK_TOOLS,
    V2_SPECS,
    registration_specs,
    select_packs,
)


class FakeCatalog:
    def __init__(self, preference=None, desktop=False, mcp=False):
        self.value = preference
        self.desktop = desktop
        self.mcp = mcp

    def preference(self, key, default=None):
        assert key == "tool_packs"
        return default if self.value is None else self.value

    def rows(self, query):
        if "execution_scopes" in query:
            assert "JOIN projects" in query
            return [{"scope_key": "desktop"}] if self.desktop else []
        if "mcp_servers_v1" in query:
            return [{"id": "configured"}] if self.mcp else []
        raise AssertionError(query)


class FakeRouter:
    def __init__(self, catalog):
        self.catalog = catalog


def test_full_surface_has_expected_pack_counts_and_names():
    assert len(V2_SPECS) == 35
    assert len(CORE_TOOLS) == 20
    assert {name: len(names) for name, names in PACK_TOOLS.items()} == {
        "core": 20, "browser": 5, "desktop": 4, "terminal": 4, "mcp": 2,
    }
    assert len(set(name for names in PACK_TOOLS.values() for name in names)) == 35
    assert DEFAULT_PACKS == ("core", "browser", "terminal")


def test_every_schema_has_optional_conversation_id_and_description_template():
    forbidden_required = {"conversation_id", "run_id", "scope_revision", "request_id", "expected_sha256", "expectedsha"}
    for name, schema in V2_SPECS.items():
        assert "conversation_id" not in schema["required"], name
        assert "conversation_id" in schema["optional"], name
        assert not forbidden_required.intersection(schema["required"]), name
        words = schema["description"].split()
        assert 40 <= len(words) <= 120, (name, len(words))
        assert "Example:" in schema["description"], name
        assert "Returns" in schema["description"], name
        assert "Use it" in schema["description"], name


def test_required_parameter_names_and_types_match_contract():
    expected = {
        "bridge.context": {}, "shell.run": {"command": "string"}, "shell.job": {},
        "file.read": {"path": "string"}, "file.edit": {"path": "string"},
        "file.write": {"path": "string", "text": "string"}, "file.list": {},
        "file.search": {"pattern": "string"}, "plan.update": {}, "task.set": {"tasks": "array"},
        "task.verify": {"task_id": "string"}, "project.note": {"kind": "string", "text": "string"},
        "conversation.handoff": {}, "change.review": {}, "change.restore": {}, "artifact.read": {},
        "artifact.add": {"path": "string"}, "screen.look": {"source": "string"},
        "git.worktree.create": {"branch": "string"}, "bridge.health": {},
        "browser.navigate": {"url": "string"}, "browser.snapshot": {},
        "browser.act": {"action": "string"}, "browser.eval": {"js": "string"}, "browser.close": {},
        "desktop.windows": {}, "desktop.launch": {"app_or_path": "string"},
        "desktop.snapshot": {"window": "integer"},
        "desktop.act": {"window": "integer", "action": "string"}, "terminal.open": {},
        "terminal.send": {"terminal_id": "string", "text": "string"},
        "terminal.read": {"terminal_id": "string"}, "terminal.close": {"terminal_id": "string"},
        "mcp.list": {}, "mcp.call": {"server_id": "string", "tool_name": "string", "arguments": "object"},
    }
    assert set(expected) == set(V2_SPECS)
    for name, fields in expected.items():
        required = V2_SPECS[name]["required"]
        assert set(required) == set(fields), name
        assert {key: value["type"] for key, value in required.items()} == fields, name
    assert V2_SPECS["shell.job"]["required"] == {}
    assert V2_SPECS["shell.job"]["optional"]["action"]["type"] == "string"
    assert V2_SPECS["screen.look"]["optional"]["window"]["type"] == "integer"
    assert V2_SPECS["screen.look"]["optional"]["marks"]["type"] == "boolean"
    assert V2_SPECS["screen.look"]["optional"]["region"]["type"] == "object"
    assert V2_SPECS["terminal.open"]["optional"]["args"]["type"] == "array"
    assert V2_SPECS["browser.act"]["optional"]["value"]["type"] == "string"
    assert "Optional context section" in V2_SPECS["bridge.context"]["optional"]["section"]["description"]
    assert {"run", "path"}.issubset(V2_SPECS["change.review"]["optional"])


def test_default_registration_is_core_browser_terminal_only():
    catalog = FakeCatalog()
    assert select_packs(catalog) == DEFAULT_PACKS
    schemas = registration_specs(FakeRouter(catalog))
    assert len(schemas) == 29
    assert "desktop.windows" not in schemas
    assert "mcp.list" not in schemas
    assert schemas["shell.run"]["timeout_ms"] == 40000
    assert schemas["shell.job"]["timeout_ms"] == 40000
    assert schemas["task.verify"]["timeout_ms"] == 40000
    assert all("conversation_id" not in schema["required"] for schema in schemas.values())


def test_desktop_profile_and_configured_selected_mcp_extend_registration():
    catalog = FakeCatalog(["browser", "terminal", "mcp"], desktop=True, mcp=True)
    selected = select_packs(catalog)
    assert selected == ("core", "browser", "terminal", "desktop", "mcp")
    schemas = registration_specs(FakeRouter(catalog))
    assert len(schemas) == 35
    assert "desktop.act" in schemas and "mcp.call" in schemas


def test_chat_scoped_desktop_profile_does_not_enable_desktop_pack():
    class ChatOnlyCatalog(FakeCatalog):
        def rows(self, query):
            if "execution_scopes" in query:
                assert "JOIN projects" in query
                return []
            return super().rows(query)

    assert "desktop" not in select_packs(ChatOnlyCatalog(["browser"], desktop=True))


def test_unconfigured_mcp_and_unprofiled_desktop_are_not_registered():
    catalog = FakeCatalog(["mcp", "desktop"], desktop=False, mcp=False)
    assert select_packs(catalog) == ("core",)
    assert len(registration_specs(FakeRouter(catalog))) == 20


@pytest.mark.parametrize("value", [["browser", "browser"], ["unknown"], "browser", [1]])
def test_invalid_preferences_are_rejected(value):
    with pytest.raises(ValueError):
        select_packs(FakeCatalog(value))


def test_core_is_always_present_even_when_preference_omits_it():
    assert select_packs(FakeCatalog(["browser"])) == ("core", "browser")


def test_registration_schemas_are_fresh_copies():
    catalog = FakeCatalog()
    first = registration_specs(FakeRouter(catalog))
    first["shell.run"]["required"]["command"]["description"] = "changed"
    assert V2_SPECS["shell.run"]["required"]["command"]["description"] != "changed"
    assert copy.deepcopy(V2_SPECS) == V2_SPECS
