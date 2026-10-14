import io
import pathlib
import sys
import tempfile
import unittest


TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import venice_recovery  # noqa: E402
from dfindexeddb import utils  # noqa: E402
from dfindexeddb.indexeddb.firefox import definitions, gecko  # noqa: E402


def padded_string(value: bytes) -> bytes:
    encoded = len(value).to_bytes(4, "little") + b"\0" * 4 + value
    return encoded + b"\0" * ((-len(encoded)) % 8)


class FirefoxExternalFileTests(unittest.TestCase):
    def test_safe_record_id_accepts_current_venice_shapes_only(self):
        self.assertEqual(venice_recovery.safe_record_id("abc-123_example"), "abc-123_example")
        for unsafe in ("", "../escape", "slash/name", "back\\name", "name.json", "a" * 201):
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(ValueError, "Unsafe conversation ID"):
                venice_recovery.safe_record_id(unsafe)

    def test_firefox_file_decoder_restores_alignment(self):
        mime = b"text/plain"
        name = b"canary.txt"
        payload = (
            (7).to_bytes(8, "little")
            + padded_string(mime)
            + (123456789).to_bytes(8, "little")
            + padded_string(name)
        )
        decoder = venice_recovery.FirefoxStructuredCloneDecoder(utils.StreamDecoder(io.BytesIO(payload)))
        value = decoder._DecodeBlobOrFile(definitions.StructuredCloneTags.FILE, 0)
        self.assertEqual(value, gecko.File(index=0, size=7, type="text/plain", name="canary.txt", last_modified=123456789))

    def test_bind_firefox_external_file_embeds_verified_bytes(self):
        raw = b"example"
        # Keep the fixture inside the checked-out workspace. Some Windows
        # sandboxes expose the process temp directory read-only.
        with tempfile.TemporaryDirectory(dir=TOOLS.parent) as directory:
            root = pathlib.Path(directory)
            (root / "17").write_bytes(raw)
            value, metadata = venice_recovery.bind_firefox_external_files(
                {"file": gecko.File(index=0, size=len(raw), type="text/plain", name="canary.txt", last_modified=1)},
                "17",
                root,
            )
        self.assertEqual(value["file"]["externalFileId"], "17")
        self.assertEqual(value["file"]["bytes"], len(raw))
        self.assertEqual(metadata[0]["name"], "canary.txt")
        self.assertNotIn("$binaryBase64", metadata[0])


if __name__ == "__main__":
    unittest.main()
