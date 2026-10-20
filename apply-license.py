#!/usr/bin/env python3
# Watermark: Living_Sin (intricos777-dot) + AI agents (Hermes, Lia, opencode) - collaborative work
# Copyright (c) 2026 Living Sin (intricos777-dot) + AI agents (Hermes, Lia, opencode) - collaborative work
# Licensed under Living Sin Proprietary License (LSPL-1.0)
# See LICENSE file for details.
"""Apply intricos777-dot's MIT licence across repos.

  1. strip  - untrack vendored dependency trees (files stay on disk)
  2. mit    - add LICENSE where absent
  3. notice - add NOTICE to copyleft repos, crediting upstream

Never touches an existing LICENSE. Never pushes. Commits locally only, so
every change is reviewable and revertible before anything leaves the machine.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

HOLDER = "intricos777-dot"
YEAR = time.strftime("%Y")
GIT_USER = "intricos777-dot"

MIT = f"""MIT License

Copyright (c) {YEAR} {HOLDER}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

DEP_TREES = (
    "node_modules", "venv", ".venv", "site-packages", "vendor", "third_party",
    "_vendor", "bower_components",
)
LICENCE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE",
                 "LICENCE.md", "COPYING", "COPYING.txt")


def git(repo: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *a],
                          capture_output=True, text=True, timeout=180)


def find_repos(roots: list[Path]) -> list[Path]:
    out: set[Path] = set()
    for base in roots:
        if not base.exists():
            continue
        if (base / ".git").exists():
            out.add(base.resolve())
            continue
        for dirpath, dirnames, _ in __import__("os").walk(base):
            if ".git" in dirnames:
                out.add(Path(dirpath).resolve())
                dirnames[:] = [d for d in dirnames if d not in
                               {".git", "node_modules", "venv", ".venv",
                                "site-packages", "__pycache__", "cache"}]
    return sorted(out)


def has_licence(repo: Path) -> Path | None:
    for n in LICENCE_NAMES:
        if (repo / n).is_file():
            return repo / n
    return None


def dep_files(repo: Path) -> dict[str, int]:
    files = git(repo, "ls-files").stdout.splitlines()
    hits: dict[str, int] = {}
    for f in files:
        parts = f.split("/")
        for p in parts[:-1]:
            if p in DEP_TREES:
                hits[p] = hits.get(p, 0) + 1
                break
    return hits


def is_copyleft(path: Path) -> str | None:
    head = path.read_text(errors="ignore")[:4000]
    m = re.search(r"\b(AGPL|GPL|LGPL|MPL|CDDL|EPL|EUPL)\b", head, re.I)
    return m.group(0) if m else None


def commit(repo: Path, msg: str, pathspec: str | None = None) -> bool:
    # Restrict to an explicit path so unrelated staged work in the same repo is
    # never swept into a licence commit.
    a = ["-c", f"user.name={GIT_USER}", "commit", "-q", "-m", msg]
    if pathspec:
        a += ["--", pathspec]
    return git(repo, *a).returncode == 0


def do_strip(repos: list[Path], do_commit: bool) -> None:
    print("== 1. strip vendored dependency trees (files stay on disk)")
    for repo in repos:
        trees = dep_files(repo)
        if not trees:
            continue
        n = sum(trees.values())
        print(f"\n  {repo}")
        print(f"    untracking {n} file(s): {', '.join(f'{k}({v})' for k,v in trees.items())}")
        for t in sorted(trees):
            git(repo, "rm", "-r", "--cached", "--quiet", t)
        gi = repo / ".gitignore"
        existing = gi.read_text() if gi.is_file() else ""
        add = [t for t in sorted(trees) if t not in existing]
        if add:
            with gi.open("a") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write("\n# dependency trees - untracked by provenance gate\n")
                for t in add:
                    fh.write(f"{t}/\n")
            print(f"    .gitignore += {', '.join(t+'/' for t in add)}")
        print(f"    NOTE: still present in git HISTORY - rewrite is a separate,")
        print(f"          destructive step that has not been done.")
        if do_commit:
            ok = commit(repo, "chore: untrack vendored dependency trees\n\n"
                              "Third-party code removed from the index; files remain\n"
                              "on disk. History still contains these blobs - a history\n"
                              "rewrite has deliberately NOT been performed.")
            print(f"    committed: {ok}")


def do_mit(repos: list[Path], do_commit: bool) -> None:
    print(f"\n== 2. apply MIT (c) {YEAR} {HOLDER}")
    added = skipped = 0
    for repo in repos:
        existing = has_licence(repo)
        if existing:
            skipped += 1
            continue
        (repo / "LICENSE").write_text(MIT)
        added += 1
        print(f"  + {repo}")
        if do_commit:
            git(repo, "add", "LICENSE")
            commit(repo, f"chore: add MIT licence (c) {YEAR} {HOLDER}",
                   pathspec="LICENSE")
    print(f"\n  added: {added}   already licensed (left untouched): {skipped}")


def do_notice(repos: list[Path], do_commit: bool) -> None:
    print("\n== 3. NOTICE for copyleft repos (upstream credit, licence unchanged)")
    for repo in repos:
        lic = has_licence(repo)
        if not lic:
            continue
        cl = is_copyleft(lic)
        if not cl:
            continue
        print(f"\n  {repo}  [{cl}]")
        body = f"""{lic.name} contains upstream {cl} text and is left unmodified.

This repository is distributed under {cl}; it is NOT relicensed as MIT, because
the original authors did not offer it under MIT terms.

Upstream copyright holders retain all rights to their own work. Anyone
redistributing this repository must continue to satisfy the {cl} obligations,
including source availability and the applicable share-alike terms.

Original {cl} text: {lic.name}
"""
        (repo / "NOTICE").write_text(body)
        print(f"    wrote NOTICE crediting upstream {cl}; {lic.name} untouched")
        if do_commit:
            git(repo, "add", "NOTICE")
            commit(repo, f"docs: add NOTICE crediting upstream {cl} authors\n\n"
                         f"{lic.name} left unmodified - copyleft obligations stand.",
                   pathspec="NOTICE")
    print()


def main() -> int:
    home = Path.home()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["strip", "mit", "notice", "all"])
    ap.add_argument("roots", nargs="*",
                    default=[str(home / "Projects"), str(home / "github-intricos777")])
    ap.add_argument("--no-commit", action="store_true",
                    help="write files but do not create commits")
    args = ap.parse_args()
    do_commit = not args.no_commit

    repos = find_repos([Path(r) for r in args.roots])
    print(f"{len(repos)} repo(s) under "
          f"{', '.join(Path(r).name for r in [Path(x) for x in args.roots])}\n")

    if args.step in ("strip", "all"):
        do_strip(repos, do_commit)
    if args.step in ("mit", "all"):
        do_mit(repos, do_commit)
    if args.step in ("notice", "all"):
        do_notice(repos, do_commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
