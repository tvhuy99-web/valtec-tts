#!/usr/bin/env python3
"""Reject Android ELF dependencies that cannot be resolved from the APK namespace."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

if len(sys.argv) < 3:
    raise SystemExit("usage: verify_android_elf_needed.py <readelf> <elf> [<elf> ...]")

readelf = sys.argv[1]
expected_sea_g2p = "libsea_g2p_rs.so"
seen_sea_g2p = False

for raw_path in sys.argv[2:]:
    path = pathlib.Path(raw_path)
    if not path.is_file():
        raise RuntimeError(f"ELF verification target does not exist: {path}")

    output = subprocess.check_output(
        [readelf, "-d", str(path)],
        text=True,
        stderr=subprocess.STDOUT,
    )
    needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", output)
    print(f"{path.name} DT_NEEDED: {needed}")

    for dependency in needed:
        if "/" in dependency or "\\" in dependency:
            raise RuntimeError(
                f"{path.name} contains a non-portable DT_NEEDED path: {dependency}"
            )
        if dependency == expected_sea_g2p:
            seen_sea_g2p = True

if not seen_sea_g2p:
    raise RuntimeError(
        f"Expected an exact DT_NEEDED entry for {expected_sea_g2p}, but none was found"
    )

print("Android ELF dependencies use portable library basenames")
