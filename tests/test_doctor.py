from __future__ import annotations

import json
from pathlib import Path

from blendersessiond import cli
from blendersessiond.discovery import BlenderDiscovery
from blendersessiond.doctor import Check, DoctorReport, build_doctor_report
from blendersessiond.state import StateDirectoryCheck


def test_json_report_has_stable_shape(monkeypatch, capsys) -> None:
    report = DoctorReport(
        platform_system="Linux",
        platform_name="Linux",
        checks=(
            Check("platform", "pass", "Linux is supported."),
            Check("blender", "pass", "Blender is available."),
            Check("state_directory", "pass", "State directory is writable."),
        ),
        blender=BlenderDiscovery(
            path="/usr/bin/blender",
            version="4.3.0",
            source="PATH",
            message="Blender is available.",
        ),
        state_directory=StateDirectoryCheck(
            path="/tmp/state/blendersessiond",
            passed=True,
            message="State directory is writable.",
        ),
    )
    monkeypatch.setattr(cli, "build_doctor_report", lambda **_kwargs: report)

    exit_code = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "schema_version": 1,
        "status": "pass",
        "platform": {"system": "Linux", "name": "Linux"},
        "checks": [
            {
                "name": "platform",
                "status": "pass",
                "message": "Linux is supported.",
            },
            {
                "name": "blender",
                "status": "pass",
                "message": "Blender is available.",
            },
            {
                "name": "state_directory",
                "status": "pass",
                "message": "State directory is writable.",
            },
        ],
        "blender": {
            "path": "/usr/bin/blender",
            "version": "4.3.0",
            "source": "PATH",
        },
        "state_dir": "/tmp/state/blendersessiond",
    }


def test_missing_blender_exits_one_without_traceback(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr(
        "blendersessiond.doctor.discover_blender",
        lambda **_kwargs: BlenderDiscovery(
            None,
            None,
            None,
            "Blender was not found. Install Blender.",
        ),
    )

    report = build_doctor_report(
        environ={"XDG_STATE_HOME": str(tmp_path)},
        system="Linux",
        home=tmp_path,
    )
    monkeypatch.setattr(cli, "build_doctor_report", lambda **_kwargs: report)

    exit_code = cli.main(["doctor"])
    output = capsys.readouterr()

    assert exit_code == 1
    assert "Blender was not found" in output.out
    assert "Traceback" not in output.out
    assert output.err == ""
    assert report.to_dict()["blender"] == {
        "path": None,
        "version": None,
        "source": None,
    }


def test_unsupported_platform_fails_even_when_other_checks_pass(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "blendersessiond.doctor.discover_blender",
        lambda **_kwargs: BlenderDiscovery(
            "/blender",
            "4.3.0",
            "--blender",
            "Blender is available.",
        ),
    )

    report = build_doctor_report(
        environ={"XDG_STATE_HOME": str(tmp_path)},
        system="Plan9",
        home=tmp_path,
    )

    assert report.status == "fail"
    assert report.checks[0].name == "platform"
    assert report.checks[0].status == "fail"
