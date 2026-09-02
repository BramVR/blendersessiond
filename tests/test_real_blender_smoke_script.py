from __future__ import annotations

import real_blender_smoke

SESSION_ID = "bss_exact-session-identity-value-123456"


def test_fenced_cli_passes_exact_session_identity(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_cli(*arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(real_blender_smoke, "_run_cli", fake_run_cli)

    result = real_blender_smoke._run_fenced_cli(
        "call",
        "get_scene_info",
        session_id=SESSION_ID,
        environment={"PATH": "test"},
        expected_codes={0},
        versioned=False,
    )

    assert result == {"ok": True}
    assert captured["arguments"] == (
        "call",
        "get_scene_info",
        "--expect-session-id",
        SESSION_ID,
    )
    assert captured["kwargs"] == {
        "environment": {"PATH": "test"},
        "expected_codes": {0},
        "versioned": False,
    }


def test_best_effort_stop_uses_exact_session_identity(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_fenced_cli(*arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return {"status": "stopped"}

    monkeypatch.setattr(
        real_blender_smoke,
        "_run_fenced_cli",
        fake_run_fenced_cli,
    )

    real_blender_smoke._best_effort_stop(
        "ci-real-blender",
        SESSION_ID,
        {"PATH": "test"},
    )

    assert captured["arguments"] == (
        "stop",
        "--name",
        "ci-real-blender",
        "--json",
    )
    assert captured["kwargs"] == {
        "session_id": SESSION_ID,
        "environment": {"PATH": "test"},
        "expected_codes": {0, 1},
    }


def test_cleanup_does_not_adopt_identity_by_session_name(
    monkeypatch,
    capsys,
) -> None:
    def unexpected_stop(*_args, **_kwargs):
        raise AssertionError("cleanup adopted authority without an exact ID")

    monkeypatch.setattr(
        real_blender_smoke,
        "_best_effort_stop",
        unexpected_stop,
    )

    real_blender_smoke._cleanup_started_session(
        "ci-real-blender",
        None,
        {},
    )

    assert capsys.readouterr().err == (
        "Cleanup skipped: start returned no exact Session identity.\n"
    )


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
        expected_session_id=SESSION_ID,
        timeout=1,
        environment={},
    )

    assert result["session"]["unsaved_changes"] is False
    assert calls == 2


def _healthy_payload(*, unsaved_changes: bool | str) -> dict:
    return {
        "status": "healthy",
        "session": {
            "session_id": SESSION_ID,
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
