"""Build and verify deterministic source and optional wheelhouse release ZIPs."""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
EXCLUDED = {".git", ".venv", ".setup-tmp", "__pycache__", ".pytest_cache", "dist", "wheelhouse"}


def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def files(root: pathlib.Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(part in EXCLUDED for part in path.relative_to(root).parts):
            yield path


def make_zip(target: pathlib.Path, source: pathlib.Path, prefix: str) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files(source):
            info = zipfile.ZipInfo(f"{prefix}/{path.relative_to(source).as_posix()}", (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"CRC failure in {target.name}: {bad}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--wheelhouse", type=pathlib.Path)
    args = parser.parse_args()
    subprocess.run([sys.executable, ROOT / "scripts" / "privacy_scan.py", ROOT], check=True)
    if args.check_only:
        print("Release preflight PASS")
        return 0
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    source_zip = output / f"venice-local-archive-v{VERSION}-source.zip"
    make_zip(source_zip, ROOT, f"venice-local-archive-v{VERSION}")
    built = [source_zip]
    if args.wheelhouse:
        wheelhouse = args.wheelhouse.resolve()
        if not list(wheelhouse.glob("*.whl")):
            raise ValueError("Wheelhouse contains no wheels")
        wheel_zip = output / f"venice-local-archive-v{VERSION}-windows-py312-wheelhouse.zip"
        make_zip(wheel_zip, wheelhouse, f"venice-local-archive-v{VERSION}-windows-py312-wheelhouse")
        built.append(wheel_zip)
    manifest = output / "SHA256SUMS"
    manifest.write_text("".join(f"{digest(path)}  {path.name}\n" for path in built), encoding="ascii")
    for path in built:
        print(f"{path.name}: {digest(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
