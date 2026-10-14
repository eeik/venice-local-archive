"""Create a shareable, content-free diagnostic report from a verified run."""

from __future__ import annotations

import argparse
import json
import pathlib


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def part_kind(value: str) -> str:
    text = value.replace("\\", "/").casefold()
    if "indexeddb" in text and "blob" in text:
        return "indexeddb-blobs"
    if "indexeddb" in text:
        return "indexeddb"
    if "local storage" in text or "localstorage" in text:
        return "local-storage"
    if "file system" in text or "filesystem" in text:
        return "site-file-system"
    if "storage/default/" in text:
        return "site-origin"
    return "browser-storage"


def build_report(run: pathlib.Path) -> dict:
    run = run.resolve()
    state = read_json(run / "run-status.json")
    manifest = read_json(run / "readable" / "manifest.json")
    verification = read_json(run / "readable" / "verification.json")
    snapshot = pathlib.Path(state["snapshot"])
    snapshot_status = read_json(snapshot / "snapshot-status.json")
    chromium = manifest.get("chromiumMetadata") or {}
    compatibility = chromium.get("compatibility") or {}
    return {
        "supportReportSchemaVersion": 1,
        "archiveManifestSchemaVersion": manifest.get("schemaVersion"),
        "browser": state.get("browser"),
        "captureComplete": state.get("status") == "complete" and state.get("phase") == "complete",
        "exportVerified": verification.get("verified") is True and not verification.get("problems"),
        "snapshot": {
            "files": snapshot_status.get("files"),
            "parts": [{"kind": part_kind(item.get("part", "")), "files": item.get("files"), "bytes": item.get("bytes")} for item in snapshot_status.get("parts", [])],
        },
        "classicStoreCounts": manifest.get("sourceStoreCounts", {}),
        "rxStoreCounts": manifest.get("rxSourceStoreCounts", {}),
        "rxSchemaVersions": sorted({name.rsplit("-", 2)[-2] for name in manifest.get("rxSourceStoreCounts", {}) if name.endswith("-documents") and len(name.rsplit("-", 2)) >= 3}),
        "encryptedLayerDepthDistribution": manifest.get("encryptedLayers", {}),
        "encryptedOuterRecordBytes": manifest.get("encryptedRecordBytes", {}),
        "wireFormatVersions": compatibility.get("wireVersions", {}),
        "decoderCompatibility": {"archiveManifestSchemaVersion": manifest.get("schemaVersion"), "v8VersionCapOverride": chromium.get("v8VersionCapOverride"), "unsignedStoreIdFallbackUses": compatibility.get("unsignedStoreIdFallbackUses")},
        "authenticationEvidence": {"authenticated": manifest.get("keyAuthentication", {}).get("authenticated", False), "keyCheckProofs": manifest.get("keyAuthentication", {}).get("keyCheckProofs", 0), "recordLayerProofs": manifest.get("keyAuthentication", {}).get("recordLayerProofs", 0)},
        "problems": {"extractionErrors": len(manifest.get("extractionErrors", [])), "warnings": len(manifest.get("warnings", [])), "orphanMessages": manifest.get("orphans", {}).get("messages", 0), "orphanImages": manifest.get("orphans", {}).get("images", 0), "verificationProblems": len(verification.get("problems", []))},
        "redaction": {"containsConversationContent": False, "containsRecordIds": False, "containsEncryptionKeys": False, "containsLocalPaths": False},
    }


def markdown(report: dict) -> str:
    lines = ["# Venice local archive diagnostic report", "", "This report intentionally contains no conversation content, record IDs, encryption keys, or local paths.", "", f"- Browser: **{report['browser']}**", f"- Capture complete: **{report['captureComplete']}**", f"- Export verified: **{report['exportVerified']}**", f"- Snapshot files: **{report['snapshot']['files']}**", "", "## Store counts", "", "```json", json.dumps({"classic": report["classicStoreCounts"], "rx": report["rxStoreCounts"]}, indent=2), "```", "", "## Encryption depth distribution", "", "```json", json.dumps(report["encryptedLayerDepthDistribution"], indent=2), "```", "", "## Encrypted outer-record byte sizes", "", "```json", json.dumps(report["encryptedOuterRecordBytes"], indent=2), "```", "", "## Schema and wire compatibility", "", "```json", json.dumps({"rxSchemaVersions": report["rxSchemaVersions"], "wireFormatVersions": report["wireFormatVersions"], "decoderCompatibility": report["decoderCompatibility"]}, indent=2), "```", "", "## Problem counts", "", "```json", json.dumps(report["problems"], indent=2), "```", ""]
    return "\n".join(lines)


def write_report(run: pathlib.Path, output: pathlib.Path) -> dict:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite diagnostic output: {output}")
    output.mkdir(parents=True)
    report = build_report(run)
    (output / "support-diagnostics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output / "support-diagnostics.md").write_text(markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(write_report(args.run, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
