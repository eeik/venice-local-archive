"""Verify a recovered Venice export without printing its private contents.

Checks store counts, chat associations, unresolved encryption wrappers, the
copied site's SHA-256 manifest when available, and optional rendered media.
"""

import argparse
import collections
import hashlib
import json
import pathlib


def contains_wrapper(value, depth=0):
    if depth > 40:
        return False
    if isinstance(value, dict):
        return "__encryptedData" in value or any(contains_wrapper(x, depth + 1) for x in value.values())
    if isinstance(value, list):
        return any(contains_wrapper(x, depth + 1) for x in value)
    return False


def hash_file(path):
    hash_ = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            hash_.update(chunk)
    return hash_.hexdigest()


def verify_snapshot(snapshot):
    status = snapshot / "snapshot-status.json"
    checks = snapshot / "snapshot-manifest.sha256"
    if not status.is_file() or not checks.is_file():
        return {"available": False, "verified": False}
    state = json.loads(status.read_text(encoding="utf-8-sig"))
    if state.get("status") != "complete":
        raise ValueError("Snapshot status is not complete")
    paths = set()
    count = 0
    for line in checks.read_text(encoding="utf-8-sig").splitlines():
        expected, separator, relative = line.partition("  ")
        if not separator or len(expected) != 64 or relative in paths:
            raise ValueError("Malformed or duplicate snapshot SHA-256 entry")
        paths.add(relative)
        target = (snapshot / relative).resolve()
        if not target.is_relative_to(snapshot.resolve()) or not target.is_file():
            raise ValueError("Snapshot SHA-256 entry points outside the copy or is missing")
        if hash_file(target) != expected.lower():
            raise ValueError(f"Snapshot SHA-256 mismatch: {relative}")
        count += 1
    if count != state["files"]:
        raise ValueError("Snapshot SHA-256 file count mismatch")
    actual = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file() and p.name not in {"snapshot-status.json", "snapshot-manifest.sha256"}}
    if actual != paths:
        raise ValueError("Snapshot file set differs from SHA-256 manifest")
    return {"available": True, "verified": True, "files": count}


def verify(export, *, check_snapshot=False):
    export = pathlib.Path(export).resolve()
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    chats = sorted((export / "chats").glob("chat-*.json"))
    if len(chats) != manifest["counts"]["conversations"]:
        problems.append("Conversation file count differs from manifest")
    conversation_ids = set()
    message_ids = set()
    message_count = 0
    image_count = 0
    unresolved = 0
    for file in chats:
        doc = json.loads(file.read_text(encoding="utf-8"))
        conversation = doc["conversation"]
        if doc.get("sourceBrowser") != manifest["browser"]:
            problems.append("Chat source browser differs from manifest")
        ident = conversation["id"]
        if ident in conversation_ids:
            problems.append("Duplicate conversation ID")
        conversation_ids.add(ident)
        if file.name != f"chat-{ident}.json":
            problems.append("Chat filename differs from ID")
        unresolved += int(contains_wrapper(conversation))
        for message in doc["messages"]:
            message_count += 1
            if message["id"] in message_ids:
                problems.append("Duplicate message ID")
            message_ids.add(message["id"])
            if message.get("conversationId") != ident:
                problems.append("Message linked to another conversation")
            unresolved += int(contains_wrapper(message))
        for image in doc["messageImages"]:
            image_count += 1
            if image.get("messageId") not in {m["id"] for m in doc["messages"]}:
                problems.append("Image linked to a message outside its chat")
            unresolved += int(contains_wrapper(image))
    if message_count != manifest["counts"]["messages"]:
        problems.append("Message count differs from manifest")
    if image_count != manifest["counts"]["messageImages"]:
        problems.append("Image count differs from manifest")
    for name, count in manifest.get("rxCounts", {}).items():
        path = export / "supplemental" / f"rx-{name}.json"
        values = json.loads(path.read_text(encoding="utf-8"))
        if len(values) != count:
            problems.append(f"RxDB count mismatch: {name}")
        unresolved += sum(contains_wrapper(x) for x in values)
    for name, count in manifest.get("counts", {}).items():
        if name in {"conversations", "messages", "messageImages", "_encryptionSettings"}:
            continue
        path = export / "supplemental" / f"classic-{name}.json"
        values = json.loads(path.read_text(encoding="utf-8"))
        if len(values) != count:
            problems.append(f"Classic count mismatch: {name}")
        unresolved += sum(contains_wrapper(x) for x in values)
    if unresolved:
        problems.append(f"Unresolved encrypted wrappers: {unresolved}")
    if not manifest.get("keyAuthentication", {}).get("authenticated"):
        problems.append("No authenticated local key proof")
    if manifest.get("extractionErrors"):
        problems.append(f"Extraction errors: {len(manifest['extractionErrors'])}")
    if manifest.get("warnings"):
        problems.append(f"Extraction warnings: {len(manifest['warnings'])}")
    orphans = manifest.get("orphans", {})
    if any(orphans.values()):
        problems.append(f"Orphan records: {sum(orphans.values())}")
    chromium = manifest.get("chromiumMetadata")
    if chromium:
        expected = sum(manifest.get("sourceStoreCounts", {}).values()) + sum(manifest.get("rxSourceStoreCounts", {}).values())
        if expected != chromium.get("activeDataRecords"):
            problems.append("Chromium active-data count differs from scanned Venice stores")
    media_counts = {}
    if (export / "message-images/manifest.json").exists():
        media = json.loads((export / "message-images/manifest.json").read_text(encoding="utf-8"))
        media_counts["messageImages"] = len(media)
        if len(media) != image_count or any(not (export / "message-images" / x["file"]).is_file() for x in media):
            problems.append("Materialized message images incomplete")
    if (export / "attachments/manifest.json").exists():
        attachments = json.loads((export / "attachments/manifest.json").read_text(encoding="utf-8"))
        media_counts["attachments"] = len(attachments)
        if any(not (export / "attachments" / x["file"]).is_file() for x in attachments):
            problems.append("Materialized attachments incomplete")
    snapshot = pathlib.Path(manifest["profileCopy"])
    snapshot_result = verify_snapshot(snapshot) if check_snapshot else {"available": (snapshot / "snapshot-manifest.sha256").exists(), "verified": False}
    if check_snapshot and snapshot_result["available"] and not snapshot_result["verified"]:
        problems.append("Snapshot checksum verification failed")
    result = {
        "browser": manifest["browser"],
        "conversationFiles": len(chats),
        "messages": message_count,
        "messageImages": image_count,
        "unresolvedEncryptedWrappers": unresolved,
        "materialized": media_counts,
        "snapshot": snapshot_result,
        "problems": sorted(set(problems)),
        "verified": not problems,
    }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("export", type=pathlib.Path)
    p.add_argument("--check-snapshot", action="store_true")
    p.add_argument("--write", action="store_true", help="Write verification.json once, or compare if it already exists")
    a = p.parse_args()
    result = verify(a.export, check_snapshot=a.check_snapshot)
    data = (json.dumps(result, indent=2) + "\n").encode("utf-8")
    if a.write:
        path = a.export / "verification.json"
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("Existing verification.json differs; leave it intact and inspect the change")
        else:
            path.write_bytes(data)
    print(json.dumps(result, indent=2))
    if not result["verified"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
