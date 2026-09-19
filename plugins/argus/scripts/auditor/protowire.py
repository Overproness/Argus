"""Just enough of the protobuf wire format to read SCIP indexes and OTLP payloads, with no dependency."""
from __future__ import annotations

import struct


def varint(b: memoryview, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        x = b[i]
        i += 1
        result |= (x & 0x7F) << shift
        if not x & 0x80:
            return result, i
        shift += 7


def fields(b: memoryview):
    """Yield (field number, wire type, value): an int for varints, a memoryview otherwise."""
    i, n = 0, len(b)
    while i < n:
        key, i = varint(b, i)
        fno, wt = key >> 3, key & 7
        if wt == 0:
            v, i = varint(b, i)
        elif wt == 2:
            ln, i = varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        else:
            raise ValueError(f"unsupported wire type {wt}")
        yield fno, wt, v


def packed(v: memoryview) -> list[int]:
    out, i = [], 0
    while i < len(v):
        x, i = varint(v, i)
        out.append(x)
    return out


def fixed64(v: memoryview) -> int:
    return struct.unpack("<Q", bytes(v))[0]


def double(v: memoryview) -> float:
    return struct.unpack("<d", bytes(v))[0]


def int64(x: int) -> int:
    """Varint-encoded int64 values arrive as unsigned 64-bit; restore the sign."""
    return x - (1 << 64) if x >= 1 << 63 else x


# --- encoding (for tests and fixtures) ---------------------------------------------

def _enc_varint(x: int) -> bytes:
    x &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = x & 0x7F
        x >>= 7
        if x:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def enc_field(fno: int, value) -> bytes:
    """Encode one field: int -> varint, bytes/str -> length-delimited, ('fixed64', n) / ('double', f)."""
    if isinstance(value, tuple) and value[0] == "fixed64":
        return _enc_varint(fno << 3 | 1) + struct.pack("<Q", value[1])
    if isinstance(value, tuple) and value[0] == "double":
        return _enc_varint(fno << 3 | 1) + struct.pack("<d", value[1])
    if isinstance(value, bool) or isinstance(value, int):
        return _enc_varint(fno << 3 | 0) + _enc_varint(int(value))
    data = value.encode() if isinstance(value, str) else bytes(value)
    return _enc_varint(fno << 3 | 2) + _enc_varint(len(data)) + data
