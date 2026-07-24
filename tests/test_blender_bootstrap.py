from __future__ import annotations

import importlib.machinery
import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def test_bootstrap_hides_splash_before_registering_addon(monkeypatch) -> None:
    events: list[tuple[str, bool]] = []
    view = SimpleNamespace(show_splash=True)
    bpy = ModuleType("bpy")
    bpy.context = SimpleNamespace(
        preferences=SimpleNamespace(view=view),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(
        sys.modules,
        "blendersessiond_vendored_blender_mcp",
        ModuleType("placeholder"),
    )

    class AddonLoader:
        @staticmethod
        def create_module(_spec):
            return None

        @staticmethod
        def exec_module(module) -> None:
            module.register = lambda: events.append(("register", view.show_splash))

    spec = importlib.machinery.ModuleSpec("fake_addon", AddonLoader())

    def fake_spec_from_file_location(_name, path):
        assert Path(path).name == "addon.py"
        return spec

    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        fake_spec_from_file_location,
    )

    bootstrap = (
        Path(__file__).parents[1]
        / "src"
        / "blendersessiond"
        / "blender_bootstrap.py"
    )
    runpy.run_path(str(bootstrap), run_name="test_blender_bootstrap")

    assert view.show_splash is False
    assert events == [("register", False)]
