"""Command-line interface for blendersessiond."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from blendersessiond.doctor import DoctorReport, build_doctor_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blendersessiond",
        description="Own Blender Sessions for agent workflows.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser(
        "doctor",
        help="Check whether this machine can host a Session.",
    )
    doctor.add_argument(
        "--blender",
        metavar="PATH",
        help="Use this Blender executable.",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Print the versioned machine-readable report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "doctor":
        report = build_doctor_report(explicit_blender=args.blender)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            _print_human_report(report)
        return 0 if report.status == "pass" else 1

    raise AssertionError(f"unhandled command: {args.command}")


def _print_human_report(report: DoctorReport) -> None:
    for check in report.checks:
        print(f"[{check.status.upper()}] {check.name}: {check.message}")

    if report.status == "pass":
        print("PASS: This machine can host a Session.")
    else:
        print("FAIL: This machine cannot host a Session until the checks pass.")
