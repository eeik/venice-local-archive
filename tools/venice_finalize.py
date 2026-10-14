"""Render recovered chats, materialize images, compare browsers, and package archives."""
import argparse
import base64
import collections
import csv
import datetime as dt
import hashlib
import json
import pathlib
import zipfile


def stamp(ms):
    if not isinstance(ms, (int, float)) or ms <= 0:
        return ""
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat()


def clean(s):
    return " ".join(str(s or "Untitled chat").split())


def extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return "bin"


def render(source):
    source = pathlib.Path(source).resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    browser = manifest["browser"]
    md = source / "markdown"
    media = source / "message-images"
    if md.exists() or media.exists():
        raise FileExistsError(f"Already rendered {source}")
    md.mkdir()
    media.mkdir()
    rows = []
    image_rows = []
    index = [f"# Venice classic chats — {browser.title()}", "", "These are reading copies. JSON files preserve all recovered fields.", ""]
    for file in sorted((source / "chats").glob("chat-*.json")):
        doc = json.loads(file.read_text(encoding="utf-8"))
        conv = doc["conversation"]
        ident = conv["id"]
        title = clean(conv.get("name"))
        image_by_message = collections.defaultdict(list)
        for index_num, image in enumerate(doc["messageImages"], 1):
            data = base64.b64decode(image["contentBinary"], validate=True)
            name = f"message-{image['messageId']}-{index_num}.{extension(data)}"
            (media / name).write_bytes(data)
            image_by_message[image["messageId"]].append(name)
            image_rows.append({"messageId": image["messageId"], "file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        lines = [f"# {title}", "", f"Source browser: {browser}", f"Created (UTC): {stamp(conv.get('createdAtUnixTimestamp'))}", f"Venice route: `/chat/classic/{ident}`", ""]
        for message in doc["messages"]:
            lines += ["---", "", f"## {clean(message.get('role')).title()}", "", f"Created (UTC): {stamp(message.get('createdAtUnixTimestamp'))}", ""]
            content = message.get("content")
            lines += [content if isinstance(content, str) and content else "_(No text in this record.)_", ""]
            for name in image_by_message[message["id"]]:
                lines += [f"![Recovered message image](../message-images/{name})", ""]
            if message.get("attachments"):
                lines += [f"Attachments preserved in JSON: {len(message['attachments'])}", ""]
        (md / f"chat-{ident}.md").write_text("\n".join(lines), encoding="utf-8")
        index.append(f"- [{title}](chat-{ident}.md) — {len(doc['messages'])} messages; {stamp(conv.get('createdAtUnixTimestamp'))}")
        rows.append({
            "sourceBrowser": browser,
            "id": ident,
            "title": title,
            "createdAtUtc": stamp(conv.get("createdAtUnixTimestamp")),
            "updatedAtUtc": stamp(conv.get("updatedAtUnixTimestamp")),
            "messages": len(doc["messages"]),
            "characterId": conv.get("characterId"),
            "json": str(file.relative_to(source)).replace("\\", "/"),
            "markdown": f"markdown/chat-{ident}.md",
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "contentSha256": hashlib.sha256(json.dumps({k: v for k, v in doc.items() if k != "sourceBrowser"}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        })
    (md / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    (media / "manifest.json").write_text(json.dumps(image_rows, indent=2) + "\n", encoding="utf-8")
    if len(rows) != manifest["counts"]["conversations"] or len(image_rows) != manifest["counts"]["messageImages"]:
        raise ValueError("Render count mismatch")
    return rows, image_rows


def package(source, target):
    if target.exists():
        raise FileExistsError(f"Archive exists: {target}")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for file in source.rglob("*"):
            if file.is_file():
                z.write(file, file.relative_to(source.parent))
    with zipfile.ZipFile(target) as z:
        bad = z.testzip()
        if bad:
            raise ValueError(f"ZIP CRC error: {bad}")
    return target.stat().st_size


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("firefox_export", type=pathlib.Path)
    p.add_argument("brave_export", type=pathlib.Path)
    p.add_argument("combined_output", type=pathlib.Path)
    args = p.parse_args()
    combined = args.combined_output.resolve()
    if combined.exists():
        raise FileExistsError(f"Combined output exists: {combined}")
    firefox, f_images = render(args.firefox_export)
    brave, b_images = render(args.brave_export)
    combined.mkdir()
    by_id = collections.defaultdict(list)
    for row in firefox + brave:
        by_id[row["id"]].append(row)
    overlap = [x for x in by_id.values() if len(x) > 1]
    timeline = sorted(firefox + brave, key=lambda x: (x["createdAtUtc"], x["sourceBrowser"], x["id"]))
    fields = ["sourceBrowser", "id", "title", "createdAtUtc", "updatedAtUtc", "messages", "characterId", "json", "markdown", "sha256", "contentSha256"]
    with (combined / "conversation-timeline.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(timeline)
    summary = {
        "firefoxConversations": len(firefox),
        "braveConversations": len(brave),
        "distinctConversationIds": len(by_id),
        "overlapIds": len(overlap),
        "overlapWithIdenticalContent": sum(x[0]["contentSha256"] == x[1]["contentSha256"] for x in overlap),
        "sourceDateRangesUtc": {"firefox": [min(x["createdAtUtc"] for x in firefox), max(x["createdAtUtc"] for x in firefox)], "brave": [min(x["createdAtUtc"] for x in brave), max(x["createdAtUtc"] for x in brave)]},
        "materializedImages": {"firefox": len(f_images), "brave": len(b_images)},
    }
    (combined / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (combined / "README.md").write_text("# Venice recovery — Firefox and Brave\n\n`conversation-timeline.csv` orders conversations by creation date and keeps the original browser source. Paths in the CSV are relative to the respective browser export. Same conversation IDs across browsers remain separate so different revisions are not lost. The browser export folders contain full JSON, Markdown reading copies, images, character definitions, settings, folders, memories, and Mind records. Raw closed-browser profile snapshots are kept separately and are required for a future re-extraction.\n", encoding="utf-8")
    sizes = {"firefox": package(args.firefox_export.resolve(), combined / "Firefox-readable.zip"), "brave": package(args.brave_export.resolve(), combined / "Brave-readable.zip")}
    print(json.dumps({"comparison": summary, "zipBytes": sizes, "output": str(combined)}, indent=2))


if __name__ == "__main__":
    main()
