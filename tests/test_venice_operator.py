import configparser
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import venice_operator as operator


class OperatorTests(unittest.TestCase):
    def work(self):
        return tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parent)

    def test_json_read_accepts_windows_powershell_utf8_bom(self):
        with self.work() as folder:
            path = pathlib.Path(folder) / "snapshot-status.json"
            path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"status": "complete", "files": 12}).encode("utf-8"))
            self.assertEqual(operator.json_read(path), {"status": "complete", "files": 12})

    def test_firefox_profile_discovery(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            profile = root / "Profiles" / "safe.default"
            (profile / "storage" / "default" / "https+++venice.ai").mkdir(parents=True)
            cfg = configparser.RawConfigParser()
            cfg["Profile0"] = {"Name": "default", "IsRelative": "1", "Path": "Profiles/safe.default", "Default": "1"}
            with (root / "profiles.ini").open("w", encoding="utf-8") as stream:
                cfg.write(stream)
            self.assertEqual(operator.discover_firefox(root), profile.resolve())

    def test_firefox_ambiguity_requires_override(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            cfg = configparser.RawConfigParser()
            for index in range(2):
                profile = root / f"p{index}"
                (profile / "storage" / "default" / "https+++venice.ai").mkdir(parents=True)
                cfg[f"Profile{index}"] = {"IsRelative": "1", "Path": f"p{index}"}
            with (root / "profiles.ini").open("w", encoding="utf-8") as stream:
                cfg.write(stream)
            with self.assertRaisesRegex(ValueError, "explicitly"):
                operator.discover_firefox(root)

    def test_firefox_install_default_resolves_ambiguity(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            cfg = configparser.RawConfigParser()
            cfg["InstallABC"] = {"Default": "p1"}
            for index in range(2):
                profile = root / f"p{index}"
                (profile / "storage" / "default" / "https+++venice.ai").mkdir(parents=True)
                cfg[f"Profile{index}"] = {"IsRelative": "1", "Path": f"p{index}"}
            with (root / "profiles.ini").open("w", encoding="utf-8") as stream:
                cfg.write(stream)
            self.assertEqual(operator.discover_firefox(root), (root / "p1").resolve())

    def test_brave_last_used_profile(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            for name in ("Default", "Profile 1"):
                indexed = root / name / "IndexedDB"
                indexed.mkdir(parents=True)
                (indexed / "https_venice.ai_0.indexeddb.leveldb").mkdir()
            (root / "Local State").write_text(json.dumps({"profile": {"last_used": "Profile 1", "info_cache": {"Default": {}, "Profile 1": {}}}}), encoding="utf-8")
            self.assertEqual(operator.discover_brave(root), (root / "Profile 1").resolve())

    def test_running_browser_refusal(self):
        args = mock.Mock(archive_root=pathlib.Path("unused"), browser="all")
        with mock.patch.object(operator, "running_browsers", return_value=["firefox"]), self.assertRaisesRegex(RuntimeError, "Close"):
            operator.preflight(args)

    def test_disk_space_check(self):
        args = mock.Mock(archive_root=pathlib.Path("unused"), browser="firefox", firefox_profile=None)
        with mock.patch.object(operator, "running_browsers", return_value=[]), mock.patch.object(operator.shutil, "which", return_value="tool"), mock.patch.object(operator, "validate_archive_root", return_value=pathlib.Path("out")), mock.patch.object(operator, "selected_profiles", return_value={"firefox": pathlib.Path("p")}), mock.patch.object(operator, "directory_size", return_value=100), mock.patch.object(operator.shutil, "disk_usage", return_value=mock.Mock(free=10)), self.assertRaisesRegex(RuntimeError, "Insufficient"):
            operator.preflight(args)

    def test_unique_run_name_does_not_collide(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            when = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
            first = operator.unique_capture_name(root, when)
            (root / f"{first}-firefox").mkdir()
            second = operator.unique_capture_name(root, when)
            self.assertNotEqual(first, second)

    def test_latest_verified_selection(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            old, new = root / "old", root / "new"
            with mock.patch.object(operator, "verified_run", side_effect=lambda p, b: p.name in {"old", "new"}):
                for path, completed in ((old, "2026-01-01"), (new, "2026-02-01")):
                    path.mkdir()
                    (path / "run-status.json").write_text(json.dumps({"completedAtUtc": completed}), encoding="utf-8")
                self.assertEqual(operator.latest_verified(root, "firefox"), new.resolve())

    def test_summary_reports_partial_failure(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            checks = {name: operator.checkpoint("PASS") for name in operator.CHECKPOINTS}
            failed = {name: operator.checkpoint() for name in operator.CHECKPOINTS}
            failed["snapshot"] = operator.checkpoint("FAIL", "run-status.json", "retry", True)
            payload = {"status": "PARTIAL", "browsers": {"firefox": {"status": "PASS", "checkpoints": checks}, "brave": {"status": "FAIL", "checkpoints": failed, "error": "copy failed", "safeRetry": "retry"}}}
            operator.write_summary(root / "summary", payload)
            text = (root / "summary" / "operator-summary.md").read_text(encoding="utf-8")
            self.assertIn("Overall: **PARTIAL**", text)
            self.assertIn("Keep the incomplete run intact", text)

    def test_summary_labels_physical_and_logical_counts(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            checks = {name: operator.checkpoint("PASS") for name in operator.CHECKPOINTS}
            payload = {"status": "PASS", "browsers": {"firefox": {"status": "PASS", "snapshotFiles": 12, "counts": {"conversations": 3, "messages": 9}, "checkpoints": checks}}}
            operator.write_summary(root / "summary", payload)
            text = (root / "summary" / "operator-summary.md").read_text(encoding="utf-8")
            self.assertIn("physical browser-storage files, not conversations", text)
            self.assertIn("conversations: **3**", text)
            self.assertIn("messages: **9**", text)

    def test_backup_preflight_failure_writes_summary(self):
        with self.work() as folder:
            archive = pathlib.Path(folder)
            args = mock.Mock(archive_root=archive, browser="all")
            with mock.patch.object(operator, "validate_archive_root", return_value=archive), mock.patch.object(operator, "preflight", side_effect=RuntimeError("browser open")):
                self.assertEqual(operator.command_backup(args), 3)
            summaries = list(archive.glob("*-operator-summary/operator-summary.json"))
            self.assertEqual(len(summaries), 1)
            payload = json.loads(summaries[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FAIL")
            self.assertEqual(payload["browsers"]["firefox"]["checkpoints"]["preflight"]["status"], "FAIL")

    def test_import_and_metadata_path_do_not_open_network(self):
        with mock.patch("socket.socket", side_effect=AssertionError("network attempted")):
            operator.unique_capture_name(pathlib.Path("."))

    def test_diagnostics_refuses_existing_output_with_safe_retry(self):
        with self.work() as folder:
            root = pathlib.Path(folder)
            output = root / "venice diag"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("untouched", encoding="utf-8")
            args = mock.Mock(archive_root=root, latest=True, run=None, output=output)
            prior = root / "verified-firefox"
            with mock.patch.object(operator, "validate_archive_root", return_value=root), mock.patch.object(operator, "latest_verified", side_effect=lambda _root, browser: prior if browser == "firefox" else None), self.assertRaisesRegex(FileExistsError, "Retry with a new path"):
                operator.command_diagnostics(args)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")


if __name__ == "__main__":
    unittest.main()
