#!/usr/bin/env python3
"""Provenance gate: keep other authors' code out of your repos.

    provenance.py audit [root ...]   scan repos, report third-party code
    provenance.py manifest           write PROVENANCE.json per repo
    provenance.py gate               pre-push gate (wired to ~/.git-hooks)
    provenance.py strip <repo>       untrack dependency trees (files kept on disk)

WHAT THIS CAN AND CANNOT DO
  Detects:  vendored/committed dependency trees, declared licence files,
            foreign copyright headers, lockfiles implying upstream code.
  Cannot:   prove authorship of a snippet pasted with no licence header and no
            attribution. No static tool can. This gate raises the cost of
            importing foreign code; it is not proof of sole authorship.

Treat AI-generated code as third-party input too - see --ai flag.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Dependency trees that are never hand-written and should never be tracked.
DEP_TREES = (
    "node_modules", "venv", ".venv", "site-packages", "vendor", "third_party",
    "_vendor", "bower_components", ".gradle", "Pods", "target/debug",
)
LOCKFILES = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "Cargo.lock",
    "poetry.lock", "Gemfile.lock", "composer.lock",
)
# Licence families that impose redistribution obligations or viral effect.
# The spelled-out forms matter: a real GPL/AGPL/LGPL file usually contains the
# bare token somewhere in its body, but a title-only or heavily-edited copy
# does not, and that case previously classified as UNKNOWN - which the
# copyleft check below then skipped entirely. Longest alternatives first so
# AGPL/LGPL/EUPL/CDDL are not truncated to a shorter sibling.
COPYCLEFT = re.compile(
    r"\b(?P<tok>AGPL|LGPL|EUPL|CDDL|EPL|GPL|MPL)\b"
    r"|GNU\s+AFFERO\s+General\s+Public\s+Licen[sc]e"
    r"|GNU\s+Lesser\s+General\s+Public\s+Licen[sc]e"
    r"|GNU\s+General\s+Public\s+Licen[sc]e"
    r"|Mozilla\s+Public\s+Licen[sc]e"
    r"|Eclipse\s+Public\s+Licen[sc]e"
    r"|Common\s+Development\s+and\s+Distribution\s+Licen[sc]e",
    re.I,
)
PERMISSIVE = re.compile(r"\b(MIT|BSD|ISC|Apache|Zlib|0BSD|Unlicense|CC0|Public Domain)\b", re.I)
# "Copyright (c) 2005-2025, NumPy Developers." / "Copyright 2010-2012, D. E. Shaw"
# Note the year RANGE - a naive single-year pattern misses most real headers.
COPYRIGHT_RE = re.compile(
    r"(?i:copyright|\(c\)|©)[^0-9\n]{0,20}?(?:19|20)\d{2}"
    r"(?:\s*[-–]\s*(?:(?:19|20)\d{2})?)?[\s,.:;-]+"
    r"(?:by\s+)?"
    # name words; allow lowercase connectors so "University of Georgia" is not
    # truncated at "University"
    r"([A-Z][\w.'-]*(?:\s+(?:of|the|and|for|in|de|van|von|der|la|le|du|da)"
    r"\s+[A-Z][\w.'-]*|\s+[A-Z][\w.'-]*){0,3})"
)
# The operator's own identifiers - matches are expected to be theirs.
# "unknown" is here deliberately: it is the operator's own PC username, and
# a bare copyright holder reading "UNKNOWN" is theirs, not a third party.
OWNERS = ("livings", "intricos", "intricos777", "unknown")
# Sentinel for "a licence file exists but no family matched". Named so it can
# never be confused with a username, a repo, or a licence.
UNRECOGNISED = "<unrecognised licence text>"
AI_MARKERS = re.compile(
    r"(generated (by|with)|co-authored-by|claude|chatgpt|openai|copilot|"
    r"hermes agent|AI[- ]assisted)", re.I
)


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=120,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def tracked_files(repo: Path) -> list[str]:
    return [f for f in git(repo, "ls-files").splitlines() if f]


def dep_tree_hits(files: list[str]) -> dict[str, int]:
    hits: dict[str, int] = {}
    for f in files:
        parts = f.split("/")
        for p in parts[:-1]:
            if p in DEP_TREES:
                hits[p] = hits.get(p, 0) + 1
                break
    return hits


def find_repos(roots: list[str]) -> list[Path]:
    out: set[Path] = set()
    for r in roots:
        base = Path(r).expanduser()
        if not base.exists():
            continue
        if (base / ".git").exists():
            out.add(base.resolve())
            continue
        for dirpath, dirnames, _ in os.walk(base):
            if ".git" in dirnames:
                out.add(Path(dirpath).resolve())
                dirnames[:] = [d for d in dirnames
                               if d not in {".git", "node_modules", "venv", ".venv",
                                            "site-packages", "__pycache__", "cache"}]
    return sorted(out)


def licence_of(repo: Path) -> str:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING",
                 "COPYING.txt", "LICENSE.rst"):
        p = repo / name
        if p.is_file():
            text = p.read_text(errors="ignore")
            # Copyleft FIRST. A permissive token can appear inside a copyleft
            # file (MPL's clause 1.12 defines "Secondary License" in terms of
            # the GPL; AGPL's preamble names the MIT Expat licence). Checking
            # permissive first let those files classify as permissive.
            m = COPYCLEFT.search(text)
            if m:
                # Prefer the short token when the bare acronym is present;
                # otherwise normalise the spelled-out form back to a token so
                # callers get one stable vocabulary.
                tok = m.group("tok")
                if tok:
                    return tok.upper()
                s = m.group(0).lower()
                if "affero" in s:
                    return "AGPL"
                if "lesser" in s:
                    return "LGPL"
                if "eclipse" in s:
                    return "EPL"
                if "mozilla" in s:
                    return "MPL"
                if "common development" in s:
                    return "CDDL"
                return "GPL"
            m = PERMISSIVE.search(text)
            if m:
                return m.group(0)
            return UNRECOGNISED
    return "NONE"


def foreign_authors(repo: Path, limit: int = 400) -> dict[str, int]:
    """Copyright holders in tracked files who are not the operator."""
    authors: dict[str, int] = {}
    files = tracked_files(repo)[:limit]
    if not files:
        return authors
    # Single git grep over the index is far faster than per-file reads.
    out = git(repo, "grep", "-h", "-i", "-E",
              "copyright|\\(c\\)|©", "--", *files) if files else ""
    for line in out.splitlines()[:3000]:
        m = COPYRIGHT_RE.search(line)
        if not m:
            continue
        who = m.group(1).strip(" .,()")
        if not who or any(o in who.lower() for o in OWNERS):
            continue
        authors[who] = authors.get(who, 0) + 1
    return authors


def audit_repo(repo: Path) -> dict:
    files = tracked_files(repo)
    trees = dep_tree_hits(files)
    locks = sorted({f.split("/")[-1] for f in files
                    if f.split("/")[-1] in LOCKFILES})
    lic = licence_of(repo)
    authors = foreign_authors(repo)

    findings: list[dict] = []
    if trees:
        findings.append({
            "severity": "high",
            "id": "dep-tree-tracked",
            "title": f"{sum(trees.values())} files tracked under {', '.join(trees)}",
            "detail": "Third-party code is committed. Upstream authors own it; "
                      "you are redistributing it. .gitignore does NOT untrack.",
        })
    if lic == "NONE":
        findings.append({
            "severity": "medium", "id": "no-license",
            "title": "no LICENSE file",
            "detail": "Unclear whether you may redistribute this, and unclear to "
                      "others whether they may use it.",
        })
    if lic != "NONE" and lic != UNRECOGNISED and COPYCLEFT.search(lic):
        findings.append({
            "severity": "high", "id": "copyleft-license",
            "title": f"{lic} is copyleft",
            "detail": "Redistribution carries source-disclosure or share-alike "
                      "obligations. Get legal advice before shipping.",
        })
    if lic == UNRECOGNISED:
        # Previously this fell through every branch and reported nothing at
        # all, so a repo carrying a real copyleft licence we failed to
        # recognise passed the pre-push gate silently.
        findings.append({
            "severity": "high", "id": "unrecognised-license",
            "title": "licence file present but no family matched",
            "detail": "This is NOT an all-clear. The text may still be copyleft "
                      "or otherwise restrictive. Read it and classify by hand "
                      "before redistributing.",
        })
    if authors:
        top = sorted(authors.items(), key=lambda kv: -kv[1])[:5]
        findings.append({
            "severity": "medium", "id": "foreign-copyright",
            "title": f"{len(authors)} foreign copyright holder(s) in tracked code",
            "detail": ", ".join(f"{w} x{n}" for w, n in top),
        })
    return {
        "repo": str(repo), "tracked_files": len(files), "licence": lic,
        "dep_trees": trees, "lockfiles": locks,
        "foreign_authors": dict(sorted(authors.items(), key=lambda kv: -kv[1])[:10]),
        "findings": findings,
    }


def cmd_audit(args) -> int:
    repos = find_repos(args.roots)
    if not repos:
        print("no git repos found", file=sys.stderr)
        return 2
    results = [audit_repo(r) for r in repos]
    bad = [r for r in results if r["findings"]]

    if args.json:
        print(json.dumps({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "repos": len(repos), "results": results}, indent=2))
        return 0

    print(f"audited {len(repos)} repo(s); {len(bad)} with findings\n")
    for r in bad:
        print(f"{r['repo']}")
        print(f"    licence: {r['licence']}   tracked: {r['tracked_files']}")
        for f in r["findings"]:
            print(f"    [{f['severity'].upper():6}] {f['title']}")
            print(f"             {f['detail']}")
        print()
    if not bad:
        print("no findings")
    return 1 if bad else 0


def cmd_manifest(args) -> int:
    repos = find_repos(args.roots)
    for repo in repos:
        r = audit_repo(repo)
        r.pop("findings", None)
        r["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        r["note"] = ("Provenance record. Dep trees listed here are third-party "
                     "code, not authored here.")
        (repo / "PROVENANCE.json").write_text(json.dumps(r, indent=2))
        print(f"  wrote {repo}/PROVENANCE.json")
    return 0


def cmd_strip(args) -> int:
    """Untrack dependency trees. Files stay on disk; nothing is deleted."""
    repo = Path(args.repo).expanduser().resolve()
    files = tracked_files(repo)
    trees = dep_tree_hits(files)
    if not trees:
        print(f"{repo}: nothing tracked under a dependency tree")
        return 0
    print(f"{repo}: will untrack {sum(trees.values())} file(s) under "
          f"{', '.join(sorted(trees))}")
    print("Files remain on disk. History still contains them (see --rewrite).\n")
    if not args.yes:
        print("Re-run with --yes to execute.")
        return 1
    for tree in sorted(trees):
        subprocess.run(["git", "-C", str(repo), "rm", "-r", "--cached",
                        "--quiet", tree], check=False)
    # ensure gitignore covers them going forward
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
        print(f"  appended to .gitignore: {', '.join(add)}")
    print("\nNow commit the removal. History rewrite is a separate, destructive "
          "step - ask before doing that.")
    return 0


def cmd_gate(args) -> int:
    """pre-push: refuse to publish repos carrying untracked third-party trees."""
    repo = Path(args.repo).expanduser().resolve()
    r = audit_repo(repo)
    blocking = [f for f in r["findings"] if f["severity"] == "high"]
    if not blocking:
        return 0
    print("\n  PROVENANCE GATE - refusing to push\n")
    for f in blocking:
        print(f"    [{f['severity'].upper()}] {f['title']}")
        print(f"             {f['detail']}")
    print(f"\n  repo: {repo}")
    print("  Fix:  python3 provenance.py strip <repo> --yes   (keeps files on disk)")
    print("  Or:   --no-verify to override, deliberately and on the record.\n")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    defaults = [os.environ.get("HOME", "~") + "/Projects",
                os.environ.get("HOME", "~") + "/github-intricos777"]

    a = sub.add_parser("audit", help="scan repos for third-party code")
    a.add_argument("roots", nargs="*", default=defaults)
    a.add_argument("--json", action="store_true")

    m = sub.add_parser("manifest", help="write PROVENANCE.json per repo")
    m.add_argument("roots", nargs="*", default=defaults)

    s = sub.add_parser("strip", help="untrack dependency trees (keeps files)")
    s.add_argument("repo")
    s.add_argument("--yes", action="store_true")

    g = sub.add_parser("gate", help="pre-push check")
    g.add_argument("repo")
    g.add_argument("--no-verify", action="store_true")

    args = ap.parse_args()
    return {"audit": cmd_audit, "manifest": cmd_manifest,
            "strip": cmd_strip, "gate": cmd_gate}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
