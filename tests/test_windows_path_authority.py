from __future__ import annotations

import csv
import os
import subprocess
from pathlib import PureWindowsPath

import pytest

from blendersessiond import windows_path_authority


class FakePathApi:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.closed: list[object] = []
        self.reparse: set[str] = set()
        self.missing: set[str] = set()
        self.security: dict[str, windows_path_authority.PathSecurity] = {}

    def open_path(self, path: str) -> str:
        if path in self.missing:
            raise FileNotFoundError(path)
        self.opened.append(path)
        return path

    def is_reparse_point(self, handle: str) -> bool:
        return handle in self.reparse

    def read_security(self, handle: str) -> windows_path_authority.PathSecurity:
        return self.security.get(
            handle,
            windows_path_authority.PathSecurity(
                owner_sid=b"user", dacl_present=True, aces=()
            ),
        )

    def trusted_sids(self) -> frozenset[bytes]:
        return frozenset({b"user", b"system", b"administrators", b"creator"})

    def close_handle(self, handle: object) -> None:
        self.closed.append(handle)


def test_guard_holds_every_ancestor_and_rejects_reparse_points() -> None:
    api = FakePathApi()
    api.reparse.add("C:\\trusted")

    with pytest.raises(windows_path_authority.PathAuthorityError, match="reparse"):
        with windows_path_authority.guard_path(
            PureWindowsPath(r"C:\trusted\state\attempt"), api=api
        ):
            pass

    assert api.opened == ["C:\\", "C:\\trusted"]
    assert api.closed == list(reversed(api.opened))


def test_guard_rejects_untrusted_write_access_on_authority_target() -> None:
    api = FakePathApi()
    api.security[r"C:\trusted\state"] = windows_path_authority.PathSecurity(
        owner_sid=b"user",
        dacl_present=True,
        aces=(
            windows_path_authority.AccessEntry(
                sid=b"other", mask=windows_path_authority.FILE_WRITE_DATA
            ),
        ),
    )

    with pytest.raises(windows_path_authority.PathAuthorityError, match="untrusted"):
        with windows_path_authority.guard_path(
            PureWindowsPath(r"C:\trusted\state"), api=api
        ):
            pass


def test_guard_allows_untrusted_read_and_holds_handles_for_body() -> None:
    api = FakePathApi()
    api.security[r"C:\trusted\state"] = windows_path_authority.PathSecurity(
        owner_sid=b"user",
        dacl_present=True,
        aces=(windows_path_authority.AccessEntry(sid=b"other", mask=0x1),),
    )

    with windows_path_authority.guard_path(
        PureWindowsPath(r"C:\trusted\state"), api=api
    ):
        assert api.closed == []
    assert api.closed == list(reversed(api.opened))


@pytest.mark.parametrize(
    "security",
    [
        windows_path_authority.PathSecurity(
            owner_sid=b"other", dacl_present=True, aces=(), dacl_protected=True
        ),
        windows_path_authority.PathSecurity(
            owner_sid=b"user", dacl_present=False, aces=(), dacl_protected=True
        ),
        windows_path_authority.PathSecurity(
            owner_sid=b"user", dacl_present=True, aces=(), dacl_protected=False
        ),
    ],
)
def test_guard_requires_trusted_owner_and_present_dacl(
    security: windows_path_authority.PathSecurity,
) -> None:
    api = FakePathApi()
    api.security[r"C:\trusted\state"] = security
    with pytest.raises(windows_path_authority.PathAuthorityError):
        with windows_path_authority.guard_path(
            PureWindowsPath(r"C:\trusted\state"), api=api
        ):
            pass


def test_guard_can_secure_the_nearest_existing_parent_before_creation() -> None:
    api = FakePathApi()
    api.missing.add(r"C:\trusted\state\new")
    api.missing.add(r"C:\trusted\state\new\attempt")

    with windows_path_authority.guard_path(
        PureWindowsPath(r"C:\trusted\state\new\attempt"),
        authority_root=PureWindowsPath(r"C:\trusted\state"),
        allow_missing=True,
        api=api,
    ):
        assert api.opened[-1] == r"C:\trusted\state"


def _harden_for_current_user(path) -> None:
    identity = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=True,
    )
    user_sid = next(csv.reader([identity.stdout.strip()]))[1]
    subprocess.run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{user_sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
        capture_output=True,
        check=True,
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path security")
def test_native_private_authority_root_is_accepted(tmp_path) -> None:
    root = tmp_path / "private-authority"
    root.mkdir()
    _harden_for_current_user(root)
    with windows_path_authority.guard_path(root):
        pass


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path security")
def test_native_inherited_untrusted_writer_is_rejected(tmp_path) -> None:
    authority = tmp_path / "private-authority"
    authority.mkdir()
    _harden_for_current_user(authority)
    parent = authority / "permissive-parent"
    parent.mkdir()
    subprocess.run(
        [
            "icacls.exe",
            str(parent),
            "/grant",
            "*S-1-1-0:(OI)(CI)M",
        ],
        capture_output=True,
        check=True,
    )
    child = parent / "inherited-child"
    child.mkdir()
    with pytest.raises(windows_path_authority.PathAuthorityError, match="untrusted"):
        with windows_path_authority.guard_path(child, authority_root=authority):
            pass


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path security")
def test_native_inherit_only_untrusted_writer_is_rejected(tmp_path) -> None:
    root = tmp_path / "inherit-only-root"
    root.mkdir()
    _harden_for_current_user(root)
    subprocess.run(
        [
            "icacls.exe",
            str(root),
            "/grant",
            "*S-1-1-0:(OI)(CI)(IO)M",
        ],
        capture_output=True,
        check=True,
    )
    with pytest.raises(windows_path_authority.PathAuthorityError, match="untrusted"):
        with windows_path_authority.guard_path(root):
            pass


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
def test_native_junction_ancestor_is_rejected(tmp_path) -> None:
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=True,
    )
    with pytest.raises(windows_path_authority.PathAuthorityError, match="reparse"):
        with windows_path_authority.guard_path(
            junction / "attempt", allow_missing=True
        ):
            pass
