# Troubleshooting

## `Refusing to overwrite diagnostic output`

The `-Output` directory already exists. This is a safety stop, not an archive failure, and no existing file was changed. Keep the earlier directory for review and retry with a new UTC-stamped destination:

```powershell
$DiagStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
.\venice.ps1 diagnostics -ArchiveRoot 'D:\Private\Venice' -Latest -Output "D:\Share\Venice-Diagnostics-$DiagStamp"
```

Always retain an incomplete run. Its `run-status.json` identifies the last phase; the operator summary includes an evidence path and safe retry command.

| Stage | Meaning | Safe action |
|---|---|---|
| preflight | Wrong Python, missing dependency/tool, running browser, ambiguous profile, unwritable output, or low disk space | Correct the reported condition; rerun `doctor` |
| snapshot | Browser opened mid-copy, source changed, copy failed, or a hash differs | Close all browsers; keep the incomplete run; rerun `backup` |
| inventory | No supported Venice storage was found or LevelDB/SQLite could not be read | Confirm the selected profile and keep the snapshot for diagnosis |
| extraction | Authentication, decoding, record, or external-file handling failed | Do not alter the snapshot; retain the run and report the exact error |
| rendering/media | A referenced attachment or image could not be materialized | Preserve the run and snapshot; inspect the named manifest |
| verification | Counts, hashes, files, labels, or problem lists disagree | Treat the run as failed; never hand-edit manifests |
| lineage comparison | A deterministic comparison did not reproduce or prior evidence was invalid | Preserve current and prior runs; rerun verification on both |
| packaging | ZIP creation, CRC, or SHA-256 failed | Preserve the readable directory; retry into a new run after checking disk health/space |

### `Unexpected UTF-8 BOM` reported after packaging

Windows PowerShell 5.1 writes UTF-8 JSON with a byte-order mark. Early v0.1.0 operator code could finish the snapshot, extraction, verification, and ZIP, then misread `snapshot-status.json` while preparing the summary and incorrectly label the packaging checkpoint `FAIL`.

Upgrade to the current toolkit before retrying. Inspect the retained run's `run-status.json`: when it says both `"status": "complete"` and `"phase": "complete"`, the browser capture itself completed. Run `verify -Run PATH-TO-RUN` to recompute its snapshot, export, ZIP CRC, and archive hash without changing it. Do not delete the run merely because the older operator summary reported this BOM error. A fresh `backup` is needed only when you require a new all-PASS operator summary and the comparison/combined outputs that the interrupted post-processing did not create.

Exit code `3` means partial success: at least one requested browser completed, but another failed. Exit code `2` means failure or failed verification. A failed browser does not prevent an independent browser from completing.

No browser profile should be copied manually while its browser is open. Do not place the archive inside this repository, a browser profile, or a cloud-synchronized public directory.
