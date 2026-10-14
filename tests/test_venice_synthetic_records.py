import base64
import json
import pathlib
import sys
import tempfile
import unittest

from nacl.secret import SecretBox
from nacl.utils import random

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import venice_duplicate_review
import venice_extract_attachments
import venice_recovery


class SyntheticRecordTests(unittest.TestCase):
    def work(self):
        return tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parent)

    def test_nested_encryption_is_fully_authenticated(self):
        key = random(SecretBox.KEY_SIZE)
        box = SecretBox(key)
        record = {"id": "synthetic-id", "value": "preserved"}
        for _ in range(4):
            ciphertext = bytes(box.encrypt(json.dumps(record).encode("utf-8")))
            record = {"id": "synthetic-id", "__encryptedData": {"encoded": base64.b64encode(ciphertext).decode("ascii")}}
        keys = venice_recovery.KeyRing([key])
        decoded, depth = venice_recovery.decrypt_layers(keys, record)
        self.assertEqual(depth, 4)
        self.assertEqual(decoded["value"], "preserved")
        self.assertEqual(keys.evidence()["recordLayerProofs"], 4)

    def test_encrypted_record_size_summary_is_content_free(self):
        sizes = {}
        ciphertext = b"x" * 73
        buckets = __import__("collections").defaultdict(list)
        venice_recovery.observe_encrypted_record_size(buckets, "conversations", {"__encryptedData": ciphertext, "id": "private-id"})
        sizes["conversations"] = venice_recovery.size_summary(buckets["conversations"])
        self.assertEqual(sizes["conversations"], {"records": 1, "min": 73, "median": 73, "p95": 73, "max": 73, "total": 73})
        self.assertNotIn("private-id", json.dumps(sizes))

    def test_text_and_image_attachments_are_materialized(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            (root / "chats").mkdir()
            png = b"\x89PNG\r\n\x1a\n" + b"synthetic"
            chat = {"conversation": {"id": "safe-id"}, "messages": [{"id": "message-1", "attachments": [{"id": "a", "type": "Text", "result": "hello"}, {"id": "b", "type": "Image", "result": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}]}]}
            (root / "chats" / "chat-safe-id.json").write_text(json.dumps(chat), encoding="utf-8")
            summary = venice_extract_attachments.extract(root)
            self.assertEqual(summary, {"attachments": 2, "images": 1, "text": 1, "bytes": len(png) + 5})
            manifest = json.loads((root / "attachments" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual({item["kind"] for item in manifest}, {"Text", "Image"})

    def test_memory_versions_preserve_identity_and_detect_change(self):
        rows = []
        for snapshot, text, source in (("one", "alpha beta gamma delta epsilon zeta", "a"), ("two", "alpha beta gamma delta epsilon zeta eta", "b")):
            words = text.split()
            rows.append({"sourceBrowser": "firefox", "snapshot": snapshot, "memoryId": "m1", "characterId": "c1", "textSha256": __import__("hashlib").sha256(text.encode()).hexdigest(), "wordCount": len(words), "sourceFile": source, "shingles": {tuple(words[index:index + 5]) for index in range(max(0, len(words) - 4))}})
        _, summary = venice_duplicate_review.memory_reports(rows)
        self.assertEqual(summary["memoryRecords"], 2)
        self.assertEqual(summary["memoryReviewReasons"], {"same-id-changed-content": 1})


if __name__ == "__main__":
    unittest.main()
