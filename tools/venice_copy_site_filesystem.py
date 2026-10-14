"""Preserve a copied browser's Venice origin file system beside readable records."""
import argparse
import contextlib
import json
import os
import pathlib
import shutil

from dfindexeddb.leveldb import record as leveldb


def copy(browser, profile, export):
    profile = pathlib.Path(profile).resolve()
    export = pathlib.Path(export).resolve()
    target = export / "site-filesystem-raw"
    if target.exists():
        raise FileExistsError(f"Already copied {target}")
    if browser == "firefox":
        origin = profile / "storage/default/https+++venice.ai/fs"
    else:
        mapping = profile / "File System/Origins"
        if not mapping.is_dir():
            return {"browser": browser, "present": False, "files": 0, "bytes": 0}
        latest = None
        with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
            for item in leveldb.FolderReader(mapping).GetRecords(use_manifest=True, use_sequence_number=True):
                if item.record.key == b"ORIGIN:https_venice.ai_0":
                    if latest is None or item.record.sequence_number > latest.record.sequence_number:
                        latest = item
        if latest is None or latest.record.record_type != 1:
            return {"browser": browser, "present": False, "files": 0, "bytes": 0}
        ident = latest.record.value.decode("ascii")
        if not ident.isdigit():
            raise ValueError("Unexpected File System origin mapping")
        origin = profile / "File System" / ident
    if not origin.is_dir():
        return {"browser": browser, "present": False, "files": 0, "bytes": 0}
    shutil.copytree(origin, target)
    source_files = sorted(p for p in origin.rglob("*") if p.is_file())
    target_files = sorted(p for p in target.rglob("*") if p.is_file())
    if len(source_files) != len(target_files) or sum(p.stat().st_size for p in source_files) != sum(p.stat().st_size for p in target_files):
        raise ValueError("Site file system copy count/size mismatch")
    manifest = {"browser": browser, "present": True, "files": len(target_files), "bytes": sum(p.stat().st_size for p in target_files), "sourceProfileCopy": str(profile)}
    (export / "site-filesystem-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("browser", choices=["firefox", "brave"])
    p.add_argument("profile_copy", type=pathlib.Path)
    p.add_argument("readable_export", type=pathlib.Path)
    a = p.parse_args()
    print(json.dumps(copy(a.browser, a.profile_copy, a.readable_export), indent=2))
