"""Register the pinned BlenderMCP addon in a factory-startup Blender."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_NAME = "blendersessiond_vendored_blender_mcp"


def main() -> None:
    addon_path = Path(__file__).with_name("vendor") / "addon.py"
    spec = importlib.util.spec_from_file_location(MODULE_NAME, addon_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load vendored addon: {addon_path}")
    addon = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = addon
    spec.loader.exec_module(addon)
    addon.register()


main()
