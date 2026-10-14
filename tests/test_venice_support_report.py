import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import venice_support_report


class SupportReportTests(unittest.TestCase):
    def test_report_excludes_paths_ids_content_and_keys(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parent) as folder:
            run = pathlib.Path(folder) / "run"
            snapshot = run / "snapshot"
            readable = run / "readable"
            snapshot.mkdir(parents=True)
            readable.mkdir()
            (run / "run-status.json").write_text(json.dumps({"status": "complete", "phase": "complete", "browser": "firefox", "snapshot": str(snapshot)}), encoding="utf-8")
            (snapshot / "snapshot-status.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"files": 2, "parts": [{"part": "storage/default/https+++venice.ai", "files": 2, "bytes": 100}]}).encode())
            private_marker = "DO-NOT-SHARE-CONTENT-OR-ID"
            manifest = {"sourceStoreCounts": {"conversations": 1}, "rxSourceStoreCounts": {"memories-2-documents": 1}, "encryptedLayers": {"conversations": {"3": 1}}, "encryptedRecordBytes": {"conversations": {"records": 1, "min": 50, "median": 50, "p95": 50, "max": 50, "total": 50}}, "keyAuthentication": {"authenticated": True, "keyCheckProofs": 1, "recordLayerProofs": 3}, "extractionErrors": [], "warnings": [], "orphans": {"messages": 0, "images": 0}, "privateTestValue": private_marker}
            (readable / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (readable / "verification.json").write_text(json.dumps({"verified": True, "problems": []}), encoding="utf-8")
            report = venice_support_report.build_report(run)
            encoded = json.dumps(report)
            self.assertNotIn(str(run), encoded)
            self.assertNotIn(private_marker, encoded)
            self.assertNotIn("encryptionKey", encoded)
            self.assertEqual(report["encryptedOuterRecordBytes"]["conversations"]["max"], 50)
            self.assertFalse(any(report["redaction"].values()))


if __name__ == "__main__":
    unittest.main()
