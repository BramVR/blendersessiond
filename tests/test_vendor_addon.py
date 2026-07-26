"""Policy pins for the vendored BlenderMCP addon."""

from __future__ import annotations

from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[1] / "src" / "blendersessiond" / "vendor"


def test_vendored_addon_contains_no_telemetry_surface() -> None:
    # Upstream ships default-on telemetry; the vendored copy deletes it, so a
    # re-pin that reintroduces any of it must fail loudly. See docs/compat.md.
    source = _VENDOR.joinpath("addon.py").read_text(encoding="utf-8").lower()
    for marker in ("telemetry", "supabase", "consent"):
        assert marker not in source, f"vendored addon reintroduced {marker!r}"


def test_patch_documents_telemetry_removal() -> None:
    patch = _VENDOR.joinpath("addon.patch").read_text(encoding="utf-8")
    assert '-            "get_telemetry_consent": self.get_telemetry_consent,' in patch
    assert "+    def get_telemetry_consent" not in patch
