"""Inventory Venice chat exports and flag possible duplicates without merging them.

The reports contain titles, IDs, source paths, and hashes, but never chat text.
They are still private artifacts. Rerunning with identical inputs produces the
same report bytes. Existing outputs are never overwritten.
"""

import argparse
import collections
import csv
import hashlib
import io
import json
import pathlib
import re
import unicodedata


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_text(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", value).split())


def attachment_signature(item):
    return {
        "type": item.get("type"),
        "resultSha256": hashlib.sha256(str(item.get("result", "")).encode("utf-8")).hexdigest(),
    }


def chat_signature(doc):
    images = collections.defaultdict(list)
    raw_images = collections.defaultdict(list)
    for image in doc.get("messageImages", []):
        images[image.get("messageId")].append(hashlib.sha256(str(image.get("contentBinary", "")).encode("ascii")).hexdigest())
        raw_images[image.get("messageId")].append(image)
    turns = []
    records = []
    informative = []
    text_parts = []
    for message in doc.get("messages", []):
        content = normalize_text(message.get("content"))
        reasoning = normalize_text(message.get("reasoningContent"))
        attachments = sorted((attachment_signature(x) for x in (message.get("attachments") or [])), key=lambda x: (str(x["type"]), x["resultSha256"]))
        payload = {"role": message.get("role"), "content": content, "reasoning": reasoning, "attachments": attachments, "images": sorted(images[message.get("id")])}
        turn_hash = digest(payload)
        turns.append(turn_hash)
        records.append(digest({
            "message": message,
            "images": sorted(raw_images[message.get("id")], key=lambda item: digest(item)),
        }))
        if len(content) + len(reasoning) >= 40 or attachments or payload["images"]:
            informative.append(turn_hash)
        text_parts.append(f"{message.get('role', '')} {content} {reasoning}")
    words = re.findall(r"\w+", " ".join(text_parts).casefold(), flags=re.UNICODE)[:12000]
    shingles = {hashlib.blake2b(" ".join(words[i:i + 5]).encode("utf-8"), digest_size=8).digest() for i in range(max(0, len(words) - 4))}
    return {
        "contentSha256": digest(turns),
        "turnHashes": turns,
        # Exact archived records, unlike turnHashes, include every message
        # field. This makes an append classification deliberately strict.
        "recordHashes": records,
        "informativeHashes": informative,
        "shingles": shingles,
        "wordCount": len(words),
    }


def load_exports(paths):
    records = []
    seen_sources = set()
    for root in paths:
        root = pathlib.Path(root).resolve()
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        browser = manifest["browser"]
        run_label = root.parent.name if root.name.casefold() == "readable" else root.name
        source = (browser, str(root).casefold())
        if source in seen_sources:
            raise ValueError(f"Duplicate source argument: {root}")
        seen_sources.add(source)
        chats = sorted((root / "chats").glob("chat-*.json"))
        if len(chats) != manifest["counts"]["conversations"]:
            raise ValueError(f"Chat file count differs from manifest: {root}")
        for file in chats:
            doc = json.loads(file.read_text(encoding="utf-8"))
            conv = doc["conversation"]
            if doc.get("sourceBrowser") != browser:
                raise ValueError(f"Source browser mismatch: {file}")
            sig = chat_signature(doc)
            records.append({
                "sourceBrowser": browser,
                "snapshot": run_label,
                "conversationId": str(conv["id"]),
                "title": normalize_text(conv.get("name")),
                "createdAtUnixMs": conv.get("createdAtUnixTimestamp") or "",
                "messageCount": len(doc["messages"]),
                "jsonPath": str(file),
                "fullJsonSha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                **sig,
            })
    records.sort(key=lambda x: (x["sourceBrowser"], x["snapshot"], x["conversationId"], x["jsonPath"]))
    return records


def load_memories(paths):
    records = []
    for root in paths:
        root = pathlib.Path(root).resolve()
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        browser = manifest["browser"]
        run_label = root.parent.name if root.name.casefold() == "readable" else root.name
        for file in sorted((root / "supplemental").glob("rx-memories-*-documents.json")):
            values = json.loads(file.read_text(encoding="utf-8"))
            if len(values) != manifest.get("rxCounts", {}).get(file.name[3:-5], 0):
                raise ValueError(f"Memory count differs from manifest: {file}")
            for item in values:
                content = normalize_text(item.get("text"))
                words = re.findall(r"\w+", content.casefold(), flags=re.UNICODE)
                shingles = {hashlib.blake2b(" ".join(words[i:i + 5]).encode("utf-8"), digest_size=8).digest() for i in range(max(0, len(words) - 4))}
                records.append({
                    "sourceBrowser": browser,
                    "snapshot": run_label,
                    "memoryId": str(item.get("id")),
                    "characterId": str(item.get("characterId") or ""),
                    "textSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "wordCount": len(words),
                    "sourceFile": str(file),
                    "shingles": shingles,
                })
    return sorted(records, key=lambda x: (x["sourceBrowser"], x["snapshot"], x["memoryId"], x["sourceFile"]))


def memory_reports(records):
    inventory_fields = ["sourceBrowser", "snapshot", "memoryId", "characterId", "textSha256", "wordCount", "sourceFile"]
    inventory = [{k: row[k] for k in inventory_fields} for row in records]
    review_fields = ["reason", "sourceA", "snapshotA", "memoryIdA", "sourceB", "snapshotB", "memoryIdB", "wordCountA", "wordCountB", "wordShingleContainment", "fileA", "fileB"]
    review = []
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            same_id = a["memoryId"] == b["memoryId"]
            same_text = a["textSha256"] == b["textSha256"] and min(a["wordCount"], b["wordCount"]) >= 3
            shared = len(a["shingles"] & b["shingles"])
            containment = shared / min(len(a["shingles"]), len(b["shingles"])) if a["shingles"] and b["shingles"] else 0.0
            if same_id:
                reason = "same-id-same-content" if same_text else "same-id-changed-content"
            elif same_text:
                reason = "same-content-different-id"
            elif min(a["wordCount"], b["wordCount"]) >= 40 and shared >= 25 and containment >= 0.85:
                reason = "possible-related-content"
            else:
                continue
            review.append({
                "reason": reason,
                "sourceA": a["sourceBrowser"], "snapshotA": a["snapshot"], "memoryIdA": a["memoryId"],
                "sourceB": b["sourceBrowser"], "snapshotB": b["snapshot"], "memoryIdB": b["memoryId"],
                "wordCountA": a["wordCount"], "wordCountB": b["wordCount"],
                "wordShingleContainment": round(containment, 4), "fileA": a["sourceFile"], "fileB": b["sourceFile"],
            })
    review.sort(key=lambda x: (x["reason"], x["sourceA"], x["snapshotA"], x["memoryIdA"], x["sourceB"], x["snapshotB"], x["memoryIdB"]))
    return {
        "memory-inventory.csv": csv_bytes(inventory, inventory_fields),
        "memory-review.csv": csv_bytes(review, review_fields),
    }, {"memoryRecords": len(records), "memoryReviewPairs": len(review), "memoryReviewReasons": dict(sorted(collections.Counter(x["reason"] for x in review).items()))}


def assess(a, b):
    same_id = a["conversationId"] == b["conversationId"]
    same_content = a["contentSha256"] == b["contentSha256"] and max(a["wordCount"], b["wordCount"]) >= 5
    if same_id and same_content:
        return "same-id-same-content", 1.0, 1.0
    if same_id:
        ar, br = a["recordHashes"], b["recordHashes"]
        if len(ar) < len(br) and br[:len(ar)] == ar:
            return "same-id-strict-extension", 1.0, 1.0
        if len(br) < len(ar) and ar[:len(br)] == br:
            return "same-id-strict-prefix", 1.0, 1.0
        return "same-id-edited-or-branched", 0.0, 0.0
    if same_content:
        return "same-content-different-id", 1.0, 1.0
    ai, bi = set(a["informativeHashes"]), set(b["informativeHashes"])
    turn_containment = len(ai & bi) / min(len(ai), len(bi)) if ai and bi else 0.0
    ash, bsh = a["shingles"], b["shingles"]
    shared = len(ash & bsh)
    shingle_containment = shared / min(len(ash), len(bsh)) if ash and bsh else 0.0
    # Require substantial shared material. These are suggestions for a human,
    # never automatic deduplication decisions.
    if (min(len(ai), len(bi)) >= 3 and turn_containment >= 0.8) or (min(a["wordCount"], b["wordCount"]) >= 100 and shared >= 30 and shingle_containment >= 0.85):
        return "possible-related-content", round(turn_containment, 4), round(shingle_containment, 4)
    return None


def csv_bytes(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def reports(records):
    inventory_fields = ["sourceBrowser", "snapshot", "conversationId", "title", "createdAtUnixMs", "messageCount", "jsonPath", "fullJsonSha256", "contentSha256"]
    inventory = [{k: row[k] for k in inventory_fields} for row in records]
    review_fields = ["reason", "sourceA", "snapshotA", "conversationIdA", "titleA", "sourceB", "snapshotB", "conversationIdB", "titleB", "messagesA", "messagesB", "turnContainment", "wordShingleContainment", "jsonA", "jsonB"]
    review = []
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            result = assess(a, b)
            if result is None:
                continue
            reason, turns, shingles = result
            review.append({
                "reason": reason,
                "sourceA": a["sourceBrowser"], "snapshotA": a["snapshot"], "conversationIdA": a["conversationId"], "titleA": a["title"],
                "sourceB": b["sourceBrowser"], "snapshotB": b["snapshot"], "conversationIdB": b["conversationId"], "titleB": b["title"],
                "messagesA": a["messageCount"], "messagesB": b["messageCount"],
                "turnContainment": turns, "wordShingleContainment": shingles,
                "jsonA": a["jsonPath"], "jsonB": b["jsonPath"],
            })
    review.sort(key=lambda x: (x["reason"], x["sourceA"], x["snapshotA"], x["conversationIdA"], x["sourceB"], x["snapshotB"], x["conversationIdB"]))
    summary = {
        "conversationRecords": len(records),
        "uniqueBrowserSnapshotIdTriples": len({(x["sourceBrowser"], x["snapshot"], x["conversationId"]) for x in records}),
        "reviewPairs": len(review),
        "reviewReasons": dict(sorted(collections.Counter(x["reason"] for x in review).items())),
        "policy": "Review only; never overwrite, merge, or discard a source record automatically.",
    }
    return {
        "conversation-inventory.csv": csv_bytes(inventory, inventory_fields),
        "duplicate-review.csv": csv_bytes(review, review_fields),
        "summary.json": (json.dumps(summary, indent=2) + "\n").encode("utf-8"),
    }, summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=pathlib.Path)
    p.add_argument("exports", nargs="+", type=pathlib.Path)
    p.add_argument("--verify-existing", action="store_true", help="Recompute and compare without writing")
    a = p.parse_args()
    output = a.output.resolve()
    files, summary = reports(load_exports(a.exports))
    memory_files, memory_summary = memory_reports(load_memories(a.exports))
    files.update(memory_files)
    summary.update(memory_summary)
    files["summary.json"] = (json.dumps(summary, indent=2) + "\n").encode("utf-8")
    if a.verify_existing:
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"Report differs on rerun: {name}")
        print(json.dumps({"verifiedIdempotent": True, **summary}, indent=2))
        return
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    output.mkdir(parents=True)
    for name, data in files.items():
        (output / name).write_bytes(data)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
