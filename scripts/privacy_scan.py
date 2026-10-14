"""Fail if a publication tree contains private or unsafe artifacts."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys


SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "dist", "wheelhouse"}
FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".ldb", ".log", ".bak", ".key", ".pem", ".p12", ".pfx", ".exe", ".dll", ".so", ".dylib", ".whl"}
FORBIDDEN_NAMES = {"current", "lock", "manifest-000001", "operator-summary.json", "operator-summary.md", "snapshot-manifest.sha256"}
TEXT_SUFFIXES = {"", ".md", ".txt", ".py", ".ps1", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".csv"}
MAX_FILE = 5 * 1024 * 1024


def scan(root: pathlib.Path) -> list[str]:
    root = root.resolve()
    problems: list[str] = []
    user = os.environ.get("USERNAME", "").lower()
    path_pattern = re.compile(r"(?i)[a-z]:[\\/]users[\\/](?!me(?:[\\/]|$)|example(?:[\\/]|$))[^\\/\s'\"]+")
    private_shape = re.compile(r'"(?:conversationId|archiveSha256|keyAuthentication|extractionErrors)"\s*:')
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.lower() in SKIP_DIRS for part in rel.parts):
            continue
        if rel.as_posix() == "scripts/privacy_scan.py":
            continue
        if path.is_symlink():
            try:
                path.resolve().relative_to(root)
            except ValueError:
                problems.append(f"escaping symlink: {rel}")
            continue
        if not path.is_file():
            continue
        lower = path.name.lower()
        if lower in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"forbidden artifact: {rel}")
            continue
        if path.stat().st_size > MAX_FILE:
            problems.append(f"unexpected large file: {rel} ({path.stat().st_size} bytes)")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            problems.append(f"unexpected file type: {rel}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"unexpected binary file: {rel}")
            continue
        if path_pattern.search(text):
            problems.append(f"absolute personal path: {rel}")
        if user and user not in {"me", "example"} and re.search(rf"(?i)(?:users[\\/]|/home/){re.escape(user)}(?:[\\/]|$)", text):
            problems.append(f"current user name/path: {rel}")
        if "Brain-Extensions" in text or "Brain Extensions" in text:
            problems.append(f"private project reference: {rel}")
        if path.suffix.lower() == ".json" and private_shape.search(text) and "tests" not in rel.parts:
            problems.append(f"private archive-shaped JSON: {rel}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    problems = scan(args.root)
    if problems:
        print("Publication scan failed:", *[f"\n- {item}" for item in problems])
        return 2
    print("Publication scan PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
