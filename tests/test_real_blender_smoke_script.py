from __future__ import annotations

import real_blender_smoke


def test_wait_for_health_waits_for_unsaved_changes(monkeypatch) -> None:
    payloads = iter(
        [
            _healthy_payload(unsaved_changes="unknown"),
            _healthy_payload(unsaved_changes=False),
        ]
    )
    calls = 0

    def fake_run_cli(*_arguments, **_kwargs):
        nonlocal calls
        calls += 1
        return next(payloads)

    monkeypatch.setattr(real_blender_smoke, "_run_cli", fake_run_cli)
    monkeypatch.setattr(real_blender_smoke.time, "sleep", lambda _seconds: None)

    result = real_blender_smoke._wait_for_health(
        "ci-real-blender",
        timeout=1,
        environment={},
    )

    assert result["session"]["unsaved_changes"] is False
    assert calls == 2


def _healthy_payload(*, unsaved_changes: bool | str) -> dict:
    return {
        "status": "healthy",
        "session": {
            "status": "healthy",
            "unsaved_changes": unsaved_changes,
            "health": {
                "status": "healthy",
                "process": {
                    "status": "healthy",
                    "alive": True,
                },
                "socket": {
                    "status": "healthy",
                    "answered": True,
                },
            },
        },
    }
