"""Policy pins for the vendored BlenderMCP addon."""

from __future__ import annotations

from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[1] / "src" / "blendersessiond" / "vendor"

# Deliberately broad: upstream ships default-on telemetry and the vendored
# copy deletes it (see docs/compat.md), so any reappearance of these words —
# even in unrelated new upstream code — must fail the suite and force a
# conscious decision during a re-pin rather than slip through.
_TELEMETRY_MARKERS = ("telemetry", "supabase", "consent")


def test_vendored_addon_contains_no_telemetry_surface() -> None:
    source = _VENDOR.joinpath("addon.py").read_text(encoding="utf-8").lower()
    for marker in _TELEMETRY_MARKERS:
        assert marker not in source, f"vendored addon reintroduced {marker!r}"


def test_patch_documents_telemetry_removal() -> None:
    lines = _VENDOR.joinpath("addon.patch").read_text(encoding="utf-8").splitlines()
    removed = [line.lower() for line in lines if line.startswith("-")]
    added = [line.lower() for line in lines if line.startswith("+")]
    assert any("get_telemetry_consent" in line for line in removed), (
        "addon.patch no longer records the telemetry removal"
    )
    for marker in _TELEMETRY_MARKERS:
        assert not any(marker in line for line in added), (
            f"addon.patch adds a line containing {marker!r}"
        )
