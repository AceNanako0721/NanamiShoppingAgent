"""Bounded input and private local-state primitives."""

import json
import os
import re
import stat
from decimal import Decimal
from pathlib import Path


class Invalid(ValueError):
    def __init__(self, code="invalid_input"):
        self.code = code
        super().__init__(code)


def strict_json(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 65536:
        raise Invalid()

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Invalid()
            result[key] = value
        return result

    try:
        value = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(Invalid())
        )
    except (ValueError, RecursionError) as exc:
        raise Invalid() from exc
    count = 0

    def check(item, depth=0):
        nonlocal count
        count += 1
        if depth > 16 or count > 2048:
            raise Invalid()
        if isinstance(item, dict):
            for key, v in item.items():
                if len(key) > 256:
                    raise Invalid()
                check(v, depth + 1)
        elif isinstance(item, list):
            for v in item:
                check(v, depth + 1)
        elif isinstance(item, float):
            import math

            if not math.isfinite(item):
                raise Invalid()

    check(value)
    return value


def money(value, currency):
    scales = {"JPY": 0, "CNY": 2}
    if currency not in scales or not isinstance(value, str):
        raise Invalid()
    if not re.fullmatch(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?", value):
        raise Invalid()
    result = Decimal(value)
    unit = Decimal(1).scaleb(-scales[currency])
    if result != result.quantize(unit):
        raise Invalid()
    return result.quantize(unit)


def private_directory(path):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.is_symlink() or (part / ".git").exists():
            raise Invalid("unsafe_state_path")
        if part.exists():
            info = part.stat()
            if info.st_uid not in (0, os.getuid()) or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise Invalid("unsafe_state_path")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Invalid("unsafe_state_permissions")
    return path


def private_file(path):
    path = Path(path)
    if path.is_symlink():
        raise Invalid("unsafe_state_path")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise Invalid("unsafe_state_permissions")
    finally:
        os.close(fd)
    return path
