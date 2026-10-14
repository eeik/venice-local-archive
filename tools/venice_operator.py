"""Offline operator interface for immutable Venice browser archives."""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
import zipfile

from venice_support_report import write_report


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
CHECKPOINTS = ("preflight", "snapshot", "inventory", "extraction", "rendering/media", "verification", "lineage comparison", "packaging")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def json_read(path: pathlib.Path) -> dict:
    # Windows PowerShell 5.1 writes `-Encoding utf8` with a BOM. Python-authored
    # JSON is BOM-less, so accept both forms consistently at every boundary.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[object], *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run([str(x) for x in command], check=True, text=True, capture_output=capture)


def python_tool(name: str, *args: object, capture: bool = False) -> subprocess.CompletedProcess:
    return run([sys.executable, TOOLS / name, *args], capture=capture)


def has_firefox_venice(profile: pathlib.Path) -> bool:
    return (profile / "storage" / "default" / "https+++venice.ai").is_dir()


def has_brave_venice(profile: pathlib.Path) -> bool:
    indexed = profile / "IndexedDB"
    return indexed.is_dir() and any(indexed.glob("https_venice.ai_0.indexeddb*"))


def discover_firefox(root: pathlib.Path | None = None) -> pathlib.Path:
    root = root or pathlib.Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    ini = root / "profiles.ini"
    if not ini.is_file():
        raise FileNotFoundError(f"Firefox profiles.ini not found: {ini}")
    cfg = configparser.RawConfigParser()
    cfg.read(ini, encoding="utf-8")
    candidates: list[pathlib.Path] = []
    defaults: list[pathlib.Path] = []
    install_defaults = {
        (root / pathlib.Path(cfg.get(section, "Default"))).resolve()
        for section in cfg.sections()
        if section.startswith("Install") and cfg.has_option(section, "Default")
    }
    for section in cfg.sections():
        if not section.startswith("Profile") or not cfg.has_option(section, "Path"):
            continue
        value = pathlib.Path(cfg.get(section, "Path"))
        profile = (root / value if cfg.getboolean(section, "IsRelative", fallback=True) else value).resolve()
        if profile.is_dir():
            if has_firefox_venice(profile):
                candidates.append(profile)
            if cfg.getboolean(section, "Default", fallback=False):
                defaults.append(profile)
    if len(candidates) == 1:
        return candidates[0]
    preferred = [p for p in candidates if p in defaults or p in install_defaults]
    if len(preferred) == 1:
        return preferred[0]
    if not candidates:
        raise FileNotFoundError("No Firefox profile containing Venice storage was found")
    raise ValueError("Multiple Firefox profiles contain Venice storage; use -FirefoxProfile explicitly")


def discover_brave(root: pathlib.Path | None = None) -> pathlib.Path:
    root = root or pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "User Data"
    state_path = root / "Local State"
    if not state_path.is_file():
        raise FileNotFoundError(f"Brave Local State not found: {state_path}")
    state = json_read(state_path)
    profiles = state.get("profile", {}).get("info_cache", {})
    names = list(profiles) or [p.name for p in root.iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))]
    candidates = [(root / name).resolve() for name in names if has_brave_venice(root / name)]
    if len(candidates) == 1:
        return candidates[0]
    last_used = state.get("profile", {}).get("last_used")
    preferred = [p for p in candidates if p.name == last_used]
    if len(preferred) == 1:
        return preferred[0]
    if not candidates:
        raise FileNotFoundError("No Brave profile containing Venice storage was found")
    raise ValueError("Multiple Brave profiles contain Venice storage; use -BraveProfile explicitly")


def running_browsers() -> list[str]:
    if os.name != "nt":
        return []
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], text=True, capture_output=True, check=False)
    text = result.stdout.lower()
    return [name for name in ("firefox", "brave", "tor") if f'"{name}.exe"' in text or (name == "tor" and '"firefox.exe"' in text and "tor browser" in text)]


def directory_size(path: pathlib.Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def validate_archive_root(path: pathlib.Path) -> pathlib.Path:
    path = path.expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("ArchiveRoot must be private and outside this Git repository")
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".write-test-{uuid.uuid4().hex}"
    probe.write_text("ok", encoding="ascii")
    probe.unlink()
    return path


def selected_profiles(args: argparse.Namespace) -> dict[str, pathlib.Path]:
    browsers = [args.browser] if args.browser != "all" else ["firefox", "brave"]
    result = {}
    for browser in browsers:
        override = getattr(args, f"{browser}_profile", None)
        result[browser] = pathlib.Path(override).resolve() if override else (discover_firefox() if browser == "firefox" else discover_brave())
        if not result[browser].is_dir():
            raise FileNotFoundError(f"{browser.title()} profile not found: {result[browser]}")
    return result


def preflight(args: argparse.Namespace) -> tuple[pathlib.Path, dict[str, pathlib.Path], dict]:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Python 3.12 is required; running {sys.version.split()[0]}")
    if not (shutil.which("pwsh") or shutil.which("powershell")):
        raise RuntimeError("PowerShell is required")
    if not shutil.which("robocopy"):
        raise RuntimeError("Robocopy is required")
    running = running_browsers()
    if running:
        raise RuntimeError("Close these browsers before capture: " + ", ".join(running))
    archive = validate_archive_root(args.archive_root)
    profiles = selected_profiles(args)
    sizes = {name: directory_size(path) for name, path in profiles.items()}
    free = shutil.disk_usage(archive).free
    required = sum(sizes.values()) * 2 + 1024**3
    if free < required:
        raise RuntimeError(f"Insufficient free space: need at least {required:,} bytes, found {free:,}")
    return archive, profiles, {"python": sys.version.split()[0], "profiles": {k: str(v) for k, v in profiles.items()}, "sourceBytes": sizes, "freeBytes": free, "requiredBytes": required}


def verified_run(run_path: pathlib.Path, browser: str | None = None) -> bool:
    try:
        state = json_read(run_path / "run-status.json")
        verification = json_read(run_path / "readable" / "verification.json")
        archive = pathlib.Path(state["archive"])
        return state.get("status") == "complete" and (browser is None or state.get("browser") == browser) and verification.get("verified") is True and not verification.get("problems") and archive.is_file() and sha256(archive) == state.get("archiveSha256")
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return False


def latest_verified(root: pathlib.Path, browser: str, exclude: set[pathlib.Path] | None = None) -> pathlib.Path | None:
    exclude = {p.resolve() for p in (exclude or set())}
    choices = []
    for status in root.glob("*/run-status.json"):
        run_path = status.parent.resolve()
        if run_path not in exclude and verified_run(run_path, browser):
            state = json_read(status)
            choices.append((state.get("completedAtUtc", ""), run_path))
    return max(choices, default=(None, None))[1]


def unique_capture_name(root: pathlib.Path, now: dt.datetime | None = None) -> str:
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    while True:
        name = f"{stamp}-{uuid.uuid4().hex[:8]}"
        if not any(root.glob(f"{name}-*")):
            return name


def checkpoint(status: str = "PENDING", evidence: str | None = None, retry: str | None = None, retain: bool = False) -> dict:
    return {"status": status, "evidence": evidence, "safeRetry": retry, "retainIncompleteRun": retain}


def write_summary(folder: pathlib.Path, payload: dict) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "operator-summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = ["# Venice backup operator summary", "", f"Overall: **{payload['status']}**", ""]
    for browser, result in payload.get("browsers", {}).items():
        lines.extend([f"## {browser.title()}", ""])
        if result.get("counts"):
            lines.extend([
                f"- snapshot files: **{result.get('snapshotFiles', 'unknown')}** (physical browser-storage files, not conversations)",
                f"- conversations: **{result['counts'].get('conversations', 0)}** (top-level records in this capture)",
                f"- messages: **{result['counts'].get('messages', 0)}** (child records joined to those conversations)",
                "",
            ])
        for stage in CHECKPOINTS:
            item = result["checkpoints"][stage]
            line = f"- {stage}: **{item['status']}**"
            if item.get("evidence"):
                line += f" — `{item['evidence']}`"
            lines.append(line)
        if result.get("error"):
            lines.extend(["", f"Error: {result['error']}", f"Safe retry: `{result['safeRetry']}`", "Keep the incomplete run intact."])
        lines.append("")
    (folder / "operator-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def backup_one(browser: str, profile: pathlib.Path, archive: pathlib.Path, capture: str, prior: pathlib.Path | None) -> dict:
    run_name = f"{capture}-{browser}"
    run_path = archive / run_name
    retry = f".\\venice.ps1 backup -ArchiveRoot '{archive}' -Browser {browser} -{browser.title()}Profile '{profile}'"
    result = {"run": str(run_path), "priorRun": str(prior) if prior else None, "checkpoints": {name: checkpoint() for name in CHECKPOINTS}}
    result["checkpoints"]["preflight"] = checkpoint("PASS", str(profile))
    try:
        command: list[object] = [sys.executable, TOOLS / "venice_backup.py", browser, "--profile", profile, "--output-root", archive, "--run-name", run_name]
        if prior:
            command.extend(["--prior-manifest", prior / "readable" / "manifest.json"])
        run(command)
        state = json_read(run_path / "run-status.json")
        manifest = json_read(run_path / "readable" / "manifest.json")
        verification = json_read(run_path / "readable" / "verification.json")
        snapshot_status = json_read(pathlib.Path(state["snapshot"]) / "snapshot-status.json")
        if not verified_run(run_path, browser):
            raise RuntimeError("completed run did not pass immutable verification")
        for stage, evidence in (("snapshot", run_path / "snapshot" / "snapshot-manifest.sha256"), ("inventory", run_path / "inventory.json"), ("extraction", run_path / "readable" / "manifest.json"), ("rendering/media", run_path / "readable"), ("verification", run_path / "readable" / "verification.json"), ("packaging", state["archive"])):
            result["checkpoints"][stage] = checkpoint("PASS", str(evidence))
        if prior:
            review = archive / f"{capture}-{browser}-lineage"
            python_tool("venice_duplicate_review.py", review, prior / "readable", run_path / "readable")
            python_tool("venice_duplicate_review.py", review, prior / "readable", run_path / "readable", "--verify-existing")
            result["lineageReview"] = str(review)
            result["checkpoints"]["lineage comparison"] = checkpoint("PASS", str(review / "summary.json"))
        else:
            result["checkpoints"]["lineage comparison"] = checkpoint("PASS", "No earlier verified run; baseline established")
        result.update({"status": "PASS", "snapshotFiles": snapshot_status.get("files"), "counts": manifest.get("counts", {}), "verification": verification, "archiveSha256": state["archiveSha256"]})
    except Exception as error:
        phase = "preflight"
        if (run_path / "run-status.json").is_file():
            phase = json_read(run_path / "run-status.json").get("phase", "snapshot")
        mapping = {"extract": "extraction", "render": "rendering/media", "verify": "verification", "package": "packaging", "complete": "packaging"}
        failed_stage = mapping.get(phase, phase if phase in CHECKPOINTS else "snapshot")
        result["checkpoints"][failed_stage] = checkpoint("FAIL", str(run_path / "run-status.json"), retry, True)
        result.update({"status": "FAIL", "error": str(error), "safeRetry": retry})
    return result


def command_doctor(args: argparse.Namespace) -> int:
    archive, profiles, details = preflight(args)
    print(json.dumps({"status": "PASS", "archiveRoot": str(archive), **details}, indent=2))
    return 0


def command_backup(args: argparse.Namespace) -> int:
    archive = validate_archive_root(args.archive_root)
    capture = unique_capture_name(archive)
    try:
        archive, profiles, preflight_details = preflight(args)
    except Exception as error:
        browsers = [args.browser] if args.browser != "all" else ["firefox", "brave"]
        retry = f".\\venice.ps1 doctor -ArchiveRoot '{archive}' -Browser {args.browser}"
        results = {}
        for browser in browsers:
            checks = {name: checkpoint() for name in CHECKPOINTS}
            checks["preflight"] = checkpoint("FAIL", "doctor output", retry, False)
            results[browser] = {"status": "FAIL", "run": None, "checkpoints": checks, "error": str(error), "safeRetry": retry}
        summary_folder = archive / f"{capture}-operator-summary"
        payload = {"schemaVersion": 1, "capture": capture, "createdAtUtc": utc_now(), "status": "FAIL", "preflight": {"error": str(error)}, "browsers": results}
        write_summary(summary_folder, payload)
        print(json.dumps({"status": "FAIL", "summary": str(summary_folder / "operator-summary.md"), "error": str(error)}, indent=2))
        return 3
    priors = {browser: latest_verified(archive, browser) for browser in profiles}
    results = {browser: backup_one(browser, profile, archive, capture, priors[browser]) for browser, profile in profiles.items()}
    passed = [browser for browser, item in results.items() if item["status"] == "PASS"]
    extras: dict[str, object] = {}
    if passed == ["firefox", "brave"]:
        firefox = pathlib.Path(results["firefox"]["run"])
        brave = pathlib.Path(results["brave"]["run"])
        cross = archive / f"{capture}-cross-browser-lineage"
        python_tool("venice_duplicate_review.py", cross, firefox / "readable", brave / "readable")
        python_tool("venice_duplicate_review.py", cross, firefox / "readable", brave / "readable", "--verify-existing")
        combined = archive / f"{capture}-combined"
        python_tool("venice_combine_runs.py", firefox, brave, combined)
        extras = {"crossBrowserReview": str(cross), "combinedArchive": str(combined), "combinedArchiveSha256": json_read(combined / "archive-checksums.json")["archiveSha256"]}
    overall = "PASS" if len(passed) == len(profiles) else ("PARTIAL" if passed else "FAIL")
    summary_folder = archive / f"{capture}-operator-summary"
    payload = {"schemaVersion": 1, "capture": capture, "createdAtUtc": utc_now(), "status": overall, "preflight": preflight_details, "browsers": results, **extras}
    write_summary(summary_folder, payload)
    print(json.dumps({"status": overall, "summary": str(summary_folder / "operator-summary.md"), "runs": {k: v["run"] for k, v in results.items()}}, indent=2))
    return 0 if overall == "PASS" else 3


def verify_run(run_path: pathlib.Path) -> dict:
    if not run_path.is_dir():
        raise FileNotFoundError(run_path)
    state = json_read(run_path / "run-status.json")
    result = json.loads(python_tool("venice_verify_export.py", run_path / "readable", "--check-snapshot", capture=True).stdout)
    archive = pathlib.Path(state["archive"])
    with zipfile.ZipFile(archive) as zipped:
        bad = zipped.testzip()
    archive_hash = sha256(archive)
    ok = result["verified"] and not bad and archive_hash == state["archiveSha256"]
    return {"run": str(run_path), "browser": state["browser"], "status": "PASS" if ok else "FAIL", "snapshot": result["snapshot"], "export": result, "zipCrc": "PASS" if not bad else f"FAIL: {bad}", "archiveSha256": archive_hash, "storedArchiveSha256": state["archiveSha256"]}


def command_verify(args: argparse.Namespace) -> int:
    archive = validate_archive_root(args.archive_root)
    if args.latest:
        runs = [run for browser in ("firefox", "brave") if (run := latest_verified(archive, browser))]
    elif args.run:
        runs = [pathlib.Path(args.run).resolve()]
    else:
        raise ValueError("Specify -Latest or -Run")
    results = [verify_run(run) for run in runs]
    print(json.dumps(results, indent=2))
    return 0 if results and all(item["status"] == "PASS" for item in results) else 2


def command_status(args: argparse.Namespace) -> int:
    archive = validate_archive_root(args.archive_root)
    rows = []
    for status_file in sorted(archive.glob("*/run-status.json")):
        state = json_read(status_file)
        manifest_path = status_file.parent / "readable" / "manifest.json"
        verification_path = status_file.parent / "readable" / "verification.json"
        manifest = json_read(manifest_path) if manifest_path.is_file() else {}
        verification = json_read(verification_path) if verification_path.is_file() else {}
        snapshot_status_path = pathlib.Path(state["snapshot"]) / "snapshot-status.json" if state.get("snapshot") else status_file.parent / "snapshot" / "snapshot-status.json"
        snapshot_status = json_read(snapshot_status_path) if snapshot_status_path.is_file() else {}
        counts = manifest.get("counts", {})
        rows.append({"run": status_file.parent.name, "browser": state.get("browser"), "status": state.get("status"), "phase": state.get("phase"), "completedAtUtc": state.get("completedAtUtc"), "snapshotFiles": snapshot_status.get("files"), "conversationRecords": counts.get("conversations", 0), "messageRecords": counts.get("messages", 0), "messageImageRecords": counts.get("messageImages", 0), "counts": counts, "verified": verification.get("verified", False), "problems": len(verification.get("problems", []))})
    print(json.dumps(rows, indent=2))
    return 0


def command_diagnostics(args: argparse.Namespace) -> int:
    archive = validate_archive_root(args.archive_root)
    if args.latest:
        runs = [run for browser in ("firefox", "brave") if (run := latest_verified(archive, browser))]
    elif args.run:
        runs = [pathlib.Path(args.run).resolve()]
    else:
        raise ValueError("Specify -Latest or -Run")
    if not runs:
        raise ValueError("No verified runs were found")
    output = args.output.resolve()
    if output.exists():
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suggestion = output.with_name(f"{output.name}-{stamp}")
        raise FileExistsError(
            f"Refusing to overwrite diagnostic output: {output}. "
            f"Existing files were not changed. Retry with a new path, for example: {suggestion}"
        )
    output.mkdir(parents=True)
    reports = []
    for run_path in runs:
        state = json_read(run_path / "run-status.json")
        destination = output / state["browser"]
        reports.append({"run": run_path.name, "browser": state["browser"], "output": str(destination), "report": write_report(run_path, destination)})
    print(json.dumps({"status": "PASS", "output": str(output), "reports": reports}, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("doctor", "backup"):
        item = sub.add_parser(name)
        item.add_argument("--archive-root", required=True, type=pathlib.Path)
        item.add_argument("--browser", choices=("all", "firefox", "brave"), default="all")
        item.add_argument("--firefox-profile", type=pathlib.Path)
        item.add_argument("--brave-profile", type=pathlib.Path)
    status = sub.add_parser("status")
    status.add_argument("--archive-root", required=True, type=pathlib.Path)
    verify = sub.add_parser("verify")
    verify.add_argument("--archive-root", required=True, type=pathlib.Path)
    verify.add_argument("--latest", action="store_true")
    verify.add_argument("--run", type=pathlib.Path)
    diagnostics = sub.add_parser("diagnostics")
    diagnostics.add_argument("--archive-root", required=True, type=pathlib.Path)
    diagnostics.add_argument("--latest", action="store_true")
    diagnostics.add_argument("--run", type=pathlib.Path)
    diagnostics.add_argument("--output", required=True, type=pathlib.Path)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        return globals()[f"command_{args.command}"](args)
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
