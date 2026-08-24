"""Plugin registration tests: manifest contract + register(ctx).

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

import yaml


class FakeContext:
    def __init__(self):
        self.hooks = []
        self.tools = {}

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_tool(self, name, **kwargs):
        self.tools[name] = kwargs


class TestManifest:
    def test_plugin_yaml_contract(self):
        import json
        from pathlib import Path

        manifest = yaml.safe_load(
            (Path(__file__).resolve().parent.parent / "plugin.yaml").read_text()
        )
        assert manifest["name"] == "skill-owner-routing"
        assert manifest["kind"] == "standalone"
        assert manifest["provides_hooks"] == ["pre_tool_call"]
        assert sorted(manifest["provides_tools"]) == [
            "skill_owner_audit",
            "skill_owner_create",
        ]
        assert manifest["license"] == "MIT"


class TestRegister:
    def test_register_one_hook_two_tools(self):
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from skill_owner_routing.register import register

        ctx = FakeContext()
        register(ctx)
        assert len(ctx.hooks) == 1
        hook_name, callback = ctx.hooks[0]
        assert hook_name == "pre_tool_call"
        assert callable(callback)
        assert set(ctx.tools) == {"skill_owner_create", "skill_owner_audit"}

    def test_registered_hook_early_bails(self):
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from skill_owner_routing.register import register

        ctx = FakeContext()
        register(ctx)
        _, callback = ctx.hooks[0]
        assert callback(tool_name="terminal", args={}) is None

    def test_audit_tool_handler_returns_json(self, tmp_path, monkeypatch):
        import json
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from skill_owner_routing import common
        from skill_owner_routing.register import register

        common.reset_caches()
        ctx = FakeContext()
        register(ctx)
        result = json.loads(ctx.tools["skill_owner_audit"]["handler"]({"action": "scan"}))
        assert result["ok"] is True
        assert result["scanned"] == 0
        common.reset_caches()

    def test_create_tool_validates_args(self):
        import json
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from skill_owner_routing.register import register

        ctx = FakeContext()
        register(ctx)
        result = json.loads(ctx.tools["skill_owner_create"]["handler"]({"name": ""}))
        assert result["success"] is False
