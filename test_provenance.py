# Watermark: Living_Sin (intricos777-dot) + AI agents (Hermes, Lia, opencode) - collaborative work
# Copyright (c) 2026 Living Sin (intricos777-dot) + AI agents (Hermes, Lia, opencode) - collaborative work
# Licensed under Living Sin Proprietary License (LSPL-1.0)
# See LICENSE file for details.
import sys, tempfile, os
from pathlib import Path
sys.path.insert(0, '/home/livings/provenance')
from provenance import licence_of, UNRECOGNISED, OWNERS

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}"
          + ("" if ok else f"  (want {want!r})"))
    if not ok:
        fails.append(label)


print("=== real repos on this box ===")
for label, path, want in [
    ("garuda-tools-fresh (GPL-3.0)", "/home/livings/Projects/garuda-tools-fresh", "GPL"),
    ("firedragon-flatpak-fresh (MPL-2.0)", "/home/livings/Projects/firedragon-flatpak-fresh", "MPL"),
    ("hermes-blood (MIT)", "/home/livings/Projects/hermes-blood", "MIT"),
]:
    check(label, licence_of(Path(path)), want)

print()
print("=== synthetic title-only headers (the reported gap) ===")
for label, text, want in [
    ("GPL-3.0 title only",
     "                    GNU GENERAL PUBLIC LICENSE\n"
     "                       Version 3, 29 June 2007\n", "GPL"),
    ("AGPL-3.0 title only",
     "                    GNU AFFERO GENERAL PUBLIC LICENSE\n"
     "                       Version 3, 19 November 2007\n", "AGPL"),
    ("LGPL-2.1 title only",
     "                  GNU LESSER GENERAL PUBLIC LICENSE\n"
     "                       Version 2.1, February 1999\n", "LGPL"),
    ("MPL spelled out",
     "Mozilla Public License Version 2.0\n", "MPL"),
    ("CDDL spelled out",
     "Common Development and Distribution License 1.0\n", "CDDL"),
    ("MIT still wins", "MIT License\n\nPermission is hereby granted\n", "MIT"),
]:
    with tempfile.TemporaryDirectory() as d:
        Path(d, "LICENSE").write_text(text)
        check(label, licence_of(Path(d)), want)

print()
print("=== unrecognised now yields a real sentinel, not a silent pass ===")
with tempfile.TemporaryDirectory() as d:
    Path(d, "LICENSE").write_text("Some bespoke terms nobody has seen.\n")
    v = licence_of(Path(d))
    check("sentinel", v, UNRECOGNISED)
    print(f"  sentinel is not a bare word: {v!r}")

print()
print("=== OWNERS covers the operator's UNKNOWN username ===")
print(f"  OWNERS = {OWNERS}")
check("unknown in OWNERS", "unknown" in OWNERS, True)

print()
print("FAILURES:", len(fails))
sys.exit(1 if fails else 0)
