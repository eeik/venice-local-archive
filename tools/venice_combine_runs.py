"""Make an immutable, source-labeled combined archive from two verified runs."""

import argparse
import csv
import hashlib
import json
import pathlib
import zipfile

from venice_finalize import clean, stamp


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(run, browser):
    run = run.resolve()
    state = json.loads((run / "run-status.json").read_text(encoding="utf-8"))
    readable = run / "readable"
    verification = json.loads((readable / "verification.json").read_text(encoding="utf-8"))
    manifest = json.loads((readable / "manifest.json").read_text(encoding="utf-8"))
    archive = run / "Venice-readable.zip"
    if state.get("status") != "complete" or state.get("browser") != browser:
        raise ValueError(f"Incomplete or wrong-browser run: {run}")
    if not verification.get("verified") or verification.get("problems"):
        raise ValueError(f"Readable export verification failed: {run}")
    if manifest.get("browser") != browser or sha256(archive) != state.get("archiveSha256"):
        raise ValueError(f"Run manifest or archive hash differs: {run}")
    rows = []
    for file in sorted((readable / "chats").glob("chat-*.json")):
        doc = json.loads(file.read_text(encoding="utf-8"))
        conv = doc["conversation"]
        if doc.get("sourceBrowser") != browser:
            raise ValueError(f"Chat source differs: {file}")
        rows.append({
            "sourceBrowser": browser,
            "captureRun": run.name,
            "sourceSnapshot": pathlib.Path(state["snapshot"]).name,
            "conversationId": str(conv["id"]),
            "title": clean(conv.get("name")),
            "createdAtUtc": stamp(conv.get("createdAtUnixTimestamp")),
            "updatedAtUtc": stamp(conv.get("updatedAtUnixTimestamp")),
            "messages": len(doc.get("messages", [])),
            "characterId": conv.get("characterId") or "",
            "jsonPathInZip": f"{browser}/readable/chats/{file.name}",
            "jsonSha256": sha256(file),
        })
    if len(rows) != manifest["counts"]["conversations"]:
        raise ValueError(f"Conversation count differs: {run}")
    return run, state, manifest, rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("firefox_run", type=pathlib.Path)
    p.add_argument("brave_run", type=pathlib.Path)
    p.add_argument("output", type=pathlib.Path)
    a = p.parse_args()
    output = a.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    firefox = load(a.firefox_run, "firefox")
    brave = load(a.brave_run, "brave")
    if output.is_relative_to(firefox[0]) or output.is_relative_to(brave[0]):
        raise ValueError("Combined output cannot be inside a source run")
    rows = sorted(firefox[3] + brave[3], key=lambda row: (row["createdAtUtc"], row["sourceBrowser"], row["conversationId"]))
    ids = {row["conversationId"] for row in rows}
    summary = {
        "firefoxRun": firefox[0].name,
        "braveRun": brave[0].name,
        "firefoxConversations": len(firefox[3]),
        "braveConversations": len(brave[3]),
        "timelineRows": len(rows),
        "distinctConversationIds": len(ids),
        "sharedConversationIdsAcrossBrowsers": len(rows) - len(ids),
        "policy": "Preserve both browser sources and every conversation; no merge or deduplication.",
    }
    output.mkdir(parents=True)
    timeline = output / "conversation-timeline.csv"
    with timeline.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# Combined Venice readable backup\n\n"
        "`conversation-timeline.csv` interleaves chat creation dates while retaining browser, capture run, and source snapshot. "
        "The ZIP contains both verified readable exports under `firefox/readable/` and `brave/readable/`. "
        "These are private plaintext. Keep the separate raw browser snapshots for future re-extraction.\n",
        encoding="utf-8",
    )
    archive = output / "Venice-Firefox-and-Brave-readable.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zipped:
        for browser, source in (("firefox", firefox), ("brave", brave)):
            for file in sorted((source[0] / "readable").rglob("*")):
                if file.is_file():
                    zipped.write(file, f"{browser}/readable/{file.relative_to(source[0] / 'readable').as_posix()}")
        for file in (timeline, output / "comparison.json", output / "README.md"):
            zipped.write(file, file.name)
    with zipfile.ZipFile(archive) as zipped:
        bad = zipped.testzip()
        if bad:
            raise ValueError(f"Combined ZIP CRC failure: {bad}")
        entries = len(zipped.infolist())
    summary.update({"archiveEntries": entries, "archiveBytes": archive.stat().st_size, "archiveSha256": sha256(archive)})
    (output / "archive-checksums.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
