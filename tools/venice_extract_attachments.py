"""Materialize embedded Venice message attachments from a readable export."""
import argparse
import base64
import hashlib
import json
import pathlib


def image_type(raw):
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "webp"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return "bin"


def extract(root):
    root = pathlib.Path(root).resolve()
    output = root / "attachments"
    if output.exists():
        raise FileExistsError(f"Attachments already extracted: {output}")
    output.mkdir()
    manifest = []
    for chat in sorted((root / "chats").glob("chat-*.json")):
        doc = json.loads(chat.read_text(encoding="utf-8"))
        for message in doc["messages"]:
            for n, attachment in enumerate(message.get("attachments") or [], 1):
                kind = attachment.get("type")
                result = attachment.get("result")
                if not isinstance(result, str):
                    raise TypeError("Unexpected attachment result type")
                if kind == "Image":
                    encoded = result.split(",", 1)[1] if result.startswith("data:") else result
                    raw = base64.b64decode(encoded, validate=True)
                    ext = image_type(raw)
                    if ext == "bin":
                        raise ValueError("Unrecognized attachment image signature")
                elif kind == "Text":
                    raw = result.encode("utf-8")
                    ext = "txt"
                else:
                    raise ValueError(f"Unexpected attachment type: {kind}")
                name = f"chat-{doc['conversation']['id']}-message-{message['id']}-{n}.{ext}"
                (output / name).write_bytes(raw)
                manifest.append({"conversationId": doc["conversation"]["id"], "messageId": message["id"], "attachmentId": attachment.get("id"), "kind": kind, "file": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"attachments": len(manifest), "images": sum(x["kind"] == "Image" for x in manifest), "text": sum(x["kind"] == "Text" for x in manifest), "bytes": sum(x["bytes"] for x in manifest)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readable_export", type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.readable_export), indent=2))
