"""Resolve Android app labels from an APK's resources.arsc.

Android's shell only exposes the label *resource id* of an app
(``labelRes=0x7f110053`` in ``cmd package resolve-activity``). The text is
looked up here in the app's compiled resource table, preferring the device
language and falling back to the default configuration.
"""

from __future__ import annotations

import re
import struct

RES_STRING_POOL = 0x0001
RES_TABLE = 0x0002
RES_TABLE_PACKAGE = 0x0200
RES_TABLE_TYPE = 0x0201

TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03

FLAG_UTF8 = 0x100
FLAG_SPARSE = 0x01
FLAG_OFFSET16 = 0x02
FLAG_COMPLEX = 0x0001
FLAG_COMPACT = 0x0008
NO_ENTRY = 0xFFFFFFFF

_LABEL_RE = re.compile(r"labelRes=0x([0-9a-fA-F]+)\s+nonLocalizedLabel=(.*?)\s+icon=")


def parse_resolve_activity(output: str) -> tuple[int | None, str | None]:
    """Return the application's ``(labelRes, nonLocalizedLabel)``."""
    section = output.split("ApplicationInfo:", 1)
    text = section[1] if len(section) == 2 else output
    match = _LABEL_RE.search(text)
    if not match:
        return None, None
    res_id = int(match.group(1), 16) or None
    label = match.group(2).strip()
    return res_id, (None if label in ("", "null") else label)


class _StringPool:
    def __init__(self, data: bytes, offset: int) -> None:
        (_type, header_size, _size, count, _styles, flags, strings_start, _sstart) = struct.unpack_from(
            "<HHIIIIII", data, offset
        )
        self._data = data
        self._utf8 = bool(flags & FLAG_UTF8)
        self._offsets = struct.unpack_from(f"<{count}I", data, offset + header_size)
        self._base = offset + strings_start

    def get(self, index: int) -> str:
        pos = self._base + self._offsets[index]
        data = self._data
        if self._utf8:
            # utf16 length, then utf8 length (each 1 or 2 bytes)
            for _ in range(2):
                length = data[pos]
                pos += 1
                if length & 0x80:
                    length = ((length & 0x7F) << 8) | data[pos]
                    pos += 1
            return data[pos : pos + length].decode("utf-8", errors="replace")
        length = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        if length & 0x8000:
            length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0]
            pos += 2
        return data[pos : pos + length * 2].decode("utf-16-le", errors="replace")


def _entry_offset(data: bytes, offset: int, header_size: int, flags: int, count: int, index: int) -> int | None:
    table = offset + header_size
    if flags & FLAG_SPARSE:
        for i in range(count):
            idx, off = struct.unpack_from("<HH", data, table + i * 4)
            if idx == index:
                return off * 4
        return None
    if index >= count:
        return None
    if flags & FLAG_OFFSET16:
        off = struct.unpack_from("<H", data, table + index * 2)[0]
        return None if off == 0xFFFF else off * 4
    off = struct.unpack_from("<I", data, table + index * 4)[0]
    return None if off == NO_ENTRY else off


def resolve_string(arsc: bytes, res_id: int, language: str | None) -> str | None:
    """Look up string resource ``res_id`` in a resources.arsc blob."""
    lang = (language or "").lower()[:2].encode()
    for _hop in range(3):  # follow up to two references
        result = _lookup(arsc, res_id, lang)
        if result is None:
            return None
        kind, value = result
        if kind == "string":
            return value
        res_id = value
    return None


def _lookup(arsc: bytes, res_id: int, lang: bytes):
    pkg_id = res_id >> 24
    type_id = (res_id >> 16) & 0xFF
    entry_index = res_id & 0xFFFF
    _t, header_size, _size, _pkgs = struct.unpack_from("<HHII", arsc, 0)
    pos = header_size
    pool: _StringPool | None = None
    candidates: dict[str, tuple] = {}
    while pos < len(arsc):
        ctype, chdr, csize = struct.unpack_from("<HHI", arsc, pos)
        if csize == 0:
            break
        if ctype == RES_STRING_POOL and pool is None:
            pool = _StringPool(arsc, pos)
        elif ctype == RES_TABLE_PACKAGE:
            pid = struct.unpack_from("<I", arsc, pos + 8)[0]
            if pid == pkg_id:
                inner = pos + chdr
                end = pos + csize
                while inner < end:
                    itype, ihdr, isize = struct.unpack_from("<HHI", arsc, inner)
                    if isize == 0:
                        break
                    if itype == RES_TABLE_TYPE and arsc[inner + 8] == type_id:
                        flags = arsc[inner + 9]
                        count, entries_start = struct.unpack_from("<II", arsc, inner + 12)
                        config = inner + 20
                        language = arsc[config + 8 : config + 10]
                        off = _entry_offset(arsc, inner, ihdr, flags, count, entry_index)
                        if off is not None:
                            key = "lang" if language == lang and lang else ("default" if language == b"\0\0" else None)
                            if key and key not in candidates:
                                candidates[key] = (inner + entries_start + off,)
                    inner += isize
        pos += csize
    if pool is None:
        return None
    for key in ("lang", "default"):
        if key in candidates:
            (entry,) = candidates[key]
            size, flags = struct.unpack_from("<HH", arsc, entry)
            if flags & FLAG_COMPACT:
                data_type = flags >> 8
                data = struct.unpack_from("<I", arsc, entry + 4)[0]
            elif flags & FLAG_COMPLEX:
                continue
            else:
                _vsize, _res0, data_type, data = struct.unpack_from("<HBBI", arsc, entry + size)
            if data_type == TYPE_STRING:
                return "string", pool.get(data)
            if data_type == TYPE_REFERENCE:
                return "ref", data
    return None
