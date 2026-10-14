"""Run one immutable, verified Venice backup from Firefox or Brave.

Use --profile with a closed browser to take a new snapshot, or --snapshot to
reprocess an existing immutable copy. Outputs are private plaintext.
"""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile


TOOLS = pathlib.Path(__file__).resolve().parent


def run_tool(name, *args):
    subprocess.run([sys.executable, str(TOOLS / name), *map(str, args)], check=True)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def status(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("browser", choices=["firefox", "brave"])
    inputs = p.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--profile", type=pathlib.Path, help="Live profile path; browser must be closed")
    inputs.add_argument("--snapshot", type=pathlib.Path, help="Existing closed-browser copy")
    p.add_argument("--output-root", required=True, type=pathlib.Path)
    p.add_argument("--run-name", help="Unique new directory name; default is UTC timestamp plus browser")
    p.add_argument("--prior-manifest", action="append", type=pathlib.Path, default=[], help="Earlier export manifest or folder for layer trend")
    p.add_argument("--key-source", action="append", type=pathlib.Path, default=[], help="Older copied profile for a prior Venice encryption key")
    p.add_argument("--allow-empty", action="store_true", help="Record an explicitly empty post-reset Venice baseline")
    a = p.parse_args()
    run_name = a.run_name or f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{a.browser}"
    if pathlib.Path(run_name).name != run_name or run_name in {".", ".."}:
        p.error("--run-name must be a single directory name")
    root = (a.output_root.resolve() / run_name).resolve()
    input_path = (a.profile or a.snapshot).resolve()
    if not input_path.is_dir():
        raise ValueError(f"Input profile or snapshot directory missing: {input_path}")
    if root.is_relative_to(input_path):
        raise ValueError("Output run cannot be inside the input profile or snapshot")
    if root.exists():
        raise FileExistsError(f"Refusing to overwrite backup run {root}")
    root.mkdir(parents=True)
    status_file = root / "run-status.json"
    state = {"status": "incomplete", "browser": a.browser, "startedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(), "run": str(root)}
    status(status_file, state)
    if a.profile:
        state["phase"] = "snapshot"
        status(status_file, state)
        snapshot = root / "snapshot"
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("PowerShell is required for a closed-browser snapshot")
        command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS / "venice_snapshot.ps1"), "-Browser", a.browser, "-Profile", str(a.profile.resolve()), "-Output", str(snapshot)]
        if a.allow_empty:
            command.append("-AllowEmpty")
        subprocess.run(command, check=True)
    else:
        snapshot = a.snapshot.resolve()
        if not snapshot.is_dir():
            raise ValueError("Snapshot directory missing")
    state["phase"] = "inventory"
    status(status_file, state)
    inventory_path = root / "inventory.json"
    run_tool("venice_inventory.py", a.browser, snapshot, "--output", inventory_path)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    user_records = [
        count for name, count in inventory.get("classicStoreCounts", {}).items()
        if name not in {"_encryptionSettings", "_rxdb_internal", "settings"}
    ] + [
        count for name, count in inventory.get("rxStoreCounts", {}).items()
        if not name.startswith(("_rxdb_internal", "rx-migration-state-meta-"))
    ] + [
        count for name, count in inventory.get("storeCounts", {}).items()
        if not name.endswith(("/_encryptionSettings", "/_rxdb_internal", "/settings"))
        and "/rx-migration-state-meta-" not in name
    ]
    if inventory["backend"] == "absent" or (a.allow_empty and not any(user_records)):
        if not a.allow_empty:
            raise ValueError("Venice data absent; use --allow-empty only for a deliberate post-reset baseline")
        state.update({"status": "no-venice-indexeddb" if inventory["backend"] == "absent" else "empty-history", "phase": "complete", "completedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(), "snapshot": str(snapshot), "inventory": str(inventory_path)})
        status(status_file, state)
        print(json.dumps({"status": state["status"], "browser": a.browser, "snapshot": state["snapshot"]}, indent=2))
        return
    readable = root / "readable"
    state["phase"] = "extract"
    status(status_file, state)
    key_args = [part for source in a.key_source for part in ("--key-source", source.resolve())]
    run_tool("venice_recovery.py", a.browser, snapshot, readable, *key_args)
    state["phase"] = "render"
    status(status_file, state)
    run_tool("venice_extract_attachments.py", readable)
    run_tool("venice_render_one.py", readable)
    state["phase"] = "verify"
    status(status_file, state)
    verify_args = [readable, "--write"]
    if (snapshot / "snapshot-manifest.sha256").is_file():
        verify_args.append("--check-snapshot")
    run_tool("venice_verify_export.py", *verify_args)
    if a.prior_manifest:
        run_tool("venice_layer_trend.py", *a.prior_manifest, readable / "manifest.json", "--output", root / "layer-trend.csv")
    state["phase"] = "package"
    status(status_file, state)
    archive = root / "Venice-readable.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zipped:
        for file in sorted(readable.rglob("*")):
            if file.is_file():
                zipped.write(file, file.relative_to(root).as_posix())
    with zipfile.ZipFile(archive) as zipped:
        bad = zipped.testzip()
        if bad:
            raise ValueError(f"Archive CRC failure: {bad}")
        entries = len(zipped.infolist())
    state.update({
        "status": "complete",
        "phase": "complete",
        "completedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "snapshot": str(snapshot),
        "readableExport": str(readable),
        "archive": str(archive),
        "archiveEntries": entries,
        "archiveBytes": archive.stat().st_size,
        "archiveSha256": sha256(archive),
    })
    status(status_file, state)
    print(json.dumps({"status": state["status"], "browser": a.browser, "archive": state["archive"], "archiveSha256": state["archiveSha256"]}, indent=2))


if __name__ == "__main__":
    main()
