from __future__ import annotations

import ctypes
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any

FILE_WRITE_DATA = 0x0002
_FILE_APPEND_DATA = 0x0004
_FILE_WRITE_EA = 0x0010
_FILE_DELETE_CHILD = 0x0040
_FILE_WRITE_ATTRIBUTES = 0x0100
_DELETE = 0x00010000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_GENERIC_ALL = 0x10000000
_GENERIC_WRITE = 0x40000000
_DANGEROUS_ACCESS = (
    FILE_WRITE_DATA
    | _FILE_APPEND_DATA
    | _FILE_WRITE_EA
    | _FILE_DELETE_CHILD
    | _FILE_WRITE_ATTRIBUTES
    | _DELETE
    | _WRITE_DAC
    | _WRITE_OWNER
    | _GENERIC_ALL
    | _GENERIC_WRITE
)

_READ_CONTROL = 0x00020000
_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_TAG_INFO = 9
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_SE_DACL_PROTECTED = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SECURITY_MAX_SID_SIZE = 68
_WIN_CREATOR_OWNER_SID = 3
_WIN_LOCAL_SYSTEM_SID = 22
_WIN_BUILTIN_ADMINISTRATORS_SID = 26
_ACCESS_ALLOWED_ACE_TYPES = {0, 5, 9, 11}
_UNSUPPORTED_ACCESS_ALLOWED_ACE_TYPES = {4}
_ACE_OBJECT_TYPE_PRESENT = 0x1
_ACE_INHERITED_OBJECT_TYPE_PRESENT = 0x2


class PathAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccessEntry:
    sid: bytes | None
    mask: int


@dataclass(frozen=True)
class PathSecurity:
    owner_sid: bytes | None
    dacl_present: bool
    aces: tuple[AccessEntry, ...]
    dacl_protected: bool = True


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("reparse_tag", wintypes.DWORD),
    ]


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("size", wintypes.WORD),
        ("ace_count", wintypes.WORD),
        ("reserved2", wintypes.WORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("ace_type", ctypes.c_ubyte),
        ("ace_flags", ctypes.c_ubyte),
        ("ace_size", wintypes.WORD),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("sid", wintypes.LPVOID), ("attributes", wintypes.DWORD)]


class _NativePathApi:
    def __init__(self) -> None:
        self.kernel = _kernel32()
        self.advapi = _advapi32()

    def open_path(self, path: str) -> int:
        handle = self.kernel.CreateFileW(
            path,
            _READ_CONTROL | _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def is_reparse_point(self, handle: int) -> bool:
        information = _FileAttributeTagInfo()
        if not self.kernel.GetFileInformationByHandleEx(
            handle,
            _FILE_ATTRIBUTE_TAG_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(information.file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)

    def read_security(self, handle: int) -> PathSecurity:
        owner = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        descriptor = wintypes.LPVOID()
        result = self.advapi.GetSecurityInfo(
            handle,
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise ctypes.WinError(result)
        try:
            control = wintypes.WORD()
            revision = wintypes.DWORD()
            if not self.advapi.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return PathSecurity(
                owner_sid=self._sid_bytes(owner.value) if owner.value else None,
                dacl_present=bool(dacl.value),
                aces=self._access_entries(dacl.value) if dacl.value else (),
                dacl_protected=bool(control.value & _SE_DACL_PROTECTED),
            )
        finally:
            if descriptor.value:
                self.kernel.LocalFree(descriptor)

    def trusted_sids(self) -> frozenset[bytes]:
        return frozenset(
            {
                self._current_user_sid(),
                self._well_known_sid(_WIN_CREATOR_OWNER_SID),
                self._well_known_sid(_WIN_LOCAL_SYSTEM_SID),
                self._well_known_sid(_WIN_BUILTIN_ADMINISTRATORS_SID),
            }
        )

    def close_handle(self, handle: int) -> None:
        self.kernel.CloseHandle(handle)

    def _current_user_sid(self) -> bytes:
        token = wintypes.HANDLE()
        if not self.advapi.OpenProcessToken(
            self.kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            self.advapi.GetTokenInformation(
                token, _TOKEN_USER, None, 0, ctypes.byref(size)
            )
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(
                token,
                _TOKEN_USER,
                buffer,
                size,
                ctypes.byref(size),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            return self._sid_bytes(user.sid)
        finally:
            self.close_handle(token.value)

    def _well_known_sid(self, sid_type: int) -> bytes:
        buffer = ctypes.create_string_buffer(_SECURITY_MAX_SID_SIZE)
        size = wintypes.DWORD(len(buffer))
        if not self.advapi.CreateWellKnownSid(
            sid_type, None, buffer, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buffer.raw[: size.value])

    def _sid_bytes(self, sid: int) -> bytes:
        if not self.advapi.IsValidSid(sid):
            raise PathAuthorityError("Setup path contains an invalid security SID.")
        size = self.advapi.GetLengthSid(sid)
        if not 0 < size <= _SECURITY_MAX_SID_SIZE:
            raise PathAuthorityError("Setup path contains an invalid security SID.")
        return ctypes.string_at(sid, size)

    def _access_entries(self, dacl: int) -> tuple[AccessEntry, ...]:
        header = ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents
        entries: list[AccessEntry] = []
        for index in range(header.ace_count):
            ace = wintypes.LPVOID()
            if not self.advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            address = ace.value
            ace_header = ctypes.cast(address, ctypes.POINTER(_AceHeader)).contents
            if ace_header.ace_type in _UNSUPPORTED_ACCESS_ALLOWED_ACE_TYPES:
                raise PathAuthorityError(
                    "Setup authority path contains an unsupported access-allowed ACE."
                )
            if ace_header.ace_type not in _ACCESS_ALLOWED_ACE_TYPES:
                continue
            mask = ctypes.cast(
                address + ctypes.sizeof(_AceHeader), ctypes.POINTER(wintypes.DWORD)
            ).contents.value
            sid_address = self._allowed_ace_sid_address(
                address, ace_header.ace_type
            )
            entries.append(
                AccessEntry(
                    sid=self._sid_bytes(sid_address) if sid_address else None,
                    mask=mask,
                )
            )
        return tuple(entries)

    @staticmethod
    def _allowed_ace_sid_address(address: int, ace_type: int) -> int | None:
        if ace_type in {0, 9}:
            return address + ctypes.sizeof(_AceHeader) + ctypes.sizeof(wintypes.DWORD)
        flags_address = (
            address + ctypes.sizeof(_AceHeader) + ctypes.sizeof(wintypes.DWORD)
        )
        object_flags = ctypes.cast(
            flags_address, ctypes.POINTER(wintypes.DWORD)
        ).contents.value
        offset = (
            ctypes.sizeof(_AceHeader)
            + ctypes.sizeof(wintypes.DWORD)
            + ctypes.sizeof(wintypes.DWORD)
        )
        if object_flags & _ACE_OBJECT_TYPE_PRESENT:
            offset += 16
        if object_flags & _ACE_INHERITED_OBJECT_TYPE_PRESENT:
            offset += 16
        return address + offset


@contextmanager
def guard_path(
    path: PureWindowsPath,
    *,
    authority_root: PureWindowsPath | None = None,
    allow_missing: bool = False,
    api: Any | None = None,
) -> Iterator[None]:
    candidate = PureWindowsPath(path)
    authority = (
        candidate if authority_root is None else PureWindowsPath(authority_root)
    )
    if not candidate.is_absolute() or not candidate.anchor:
        raise PathAuthorityError("Setup authority paths must be absolute.")
    if (
        not authority.is_absolute()
        or ".." in candidate.parts
        or ".." in authority.parts
    ):
        raise PathAuthorityError("Setup authority paths cannot contain '..'.")
    components = _path_components(candidate)
    try:
        authority_index = components.index(authority)
    except ValueError as error:
        raise PathAuthorityError(
            "Setup authority root must contain the protected path."
        ) from error
    owner = _NativePathApi() if api is None else api
    handles: list[object] = []
    try:
        for component in components:
            try:
                handle = owner.open_path(str(component))
            except FileNotFoundError:
                if allow_missing:
                    break
                raise
            handles.append(handle)
            if owner.is_reparse_point(handle):
                raise PathAuthorityError(
                    "Setup authority paths cannot contain reparse points."
                )
        if not handles:
            raise PathAuthorityError("Setup authority path has no existing anchor.")
        if authority_index >= len(handles):
            raise PathAuthorityError(
                "Setup authority root must exist before staging."
            )
        if not allow_missing and len(handles) != len(components):
            raise FileNotFoundError(str(candidate))
        trusted = owner.trusted_sids()
        _validate_security(
            owner.read_security(handles[authority_index]),
            trusted,
            require_protected=True,
        )
        if authority_index != len(handles) - 1:
            _validate_security(
                owner.read_security(handles[-1]),
                trusted,
                require_protected=False,
            )
        yield
    finally:
        for handle in reversed(handles):
            owner.close_handle(handle)


def _path_components(path: PureWindowsPath) -> tuple[PureWindowsPath, ...]:
    current = PureWindowsPath(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    return tuple(components)


def _validate_security(
    security: PathSecurity,
    trusted_sids: frozenset[bytes],
    *,
    require_protected: bool,
) -> None:
    if security.owner_sid not in trusted_sids:
        raise PathAuthorityError("Setup authority path has an untrusted owner.")
    if not security.dacl_present:
        raise PathAuthorityError("Setup authority path must have a non-null DACL.")
    if require_protected and not security.dacl_protected:
        raise PathAuthorityError(
            "Setup authority root must disable inherited permissions."
        )
    for entry in security.aces:
        if entry.mask & _DANGEROUS_ACCESS and entry.sid not in trusted_sids:
            raise PathAuthorityError(
                "Setup authority path grants write access to an untrusted principal."
            )


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    return kernel


def _advapi32():
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.CreateWellKnownSid.restype = wintypes.BOOL
    advapi.IsValidSid.argtypes = [wintypes.LPVOID]
    advapi.IsValidSid.restype = wintypes.BOOL
    advapi.GetLengthSid.argtypes = [wintypes.LPVOID]
    advapi.GetLengthSid.restype = wintypes.DWORD
    advapi.GetAce.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi.GetAce.restype = wintypes.BOOL
    return advapi
