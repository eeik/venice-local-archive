# Operator guide

## First-time preparation

Install Python 3.12 and run `setup.ps1` while online. For an offline machine, obtain the matching release wheelhouse first and run `setup.ps1 -Wheelhouse PATH`; installation then uses the complete pinned lock with `pip --no-deps --no-index`. Keep the toolkit and private archive in different directories.

## Every capture

1. Finish any Venice conversations and wait for the browser to settle.
2. Close Firefox, Brave, and Tor Browser. Confirm they are absent in Task Manager.
3. Run `doctor`. Fix every failure before continuing.
4. Run `backup` once. Do not reopen either browser while it runs.
5. Open the path printed as `summary`.
6. Confirm preflight, snapshot, inventory, extraction, rendering/media, verification, lineage comparison, and packaging are all `PASS` for each requested browser.
7. Run `verify -Latest` when you want an independent read-only recheck.
8. Reopen browsers.

The archive root receives separate immutable browser runs, lineage reports, a cross-browser report, a combined readable archive, and an operator summary. A failed directory is evidence: do not rename, edit, or delete it. Fix the cause and run `backup` again; the random suffix guarantees a new destination.

## Reading the summary counts

Do not interpret `snapshot files` as conversations. Snapshot files are physical SQLite, LevelDB, blob, and browser File System pieces. `conversations` are top-level records recovered from the selected profile at capture time; `messages` are child records joined into them. Memories, Minds, Characters, media, and attachments are counted independently.

The newest profile can contain fewer conversations than an older archived run. That is expected after site-data clearing, account changes, or starting fresh: immutable older runs remain part of the archive. See [Archive format notes](format.md#count-vocabulary) for the definitions of logical conversations, immutable versions, and occurrences.

## Commands

```powershell
.\venice.ps1 doctor -ArchiveRoot 'D:\Private\Venice'
.\venice.ps1 backup -ArchiveRoot 'D:\Private\Venice' -Browser all
.\venice.ps1 status -ArchiveRoot 'D:\Private\Venice'
.\venice.ps1 verify -ArchiveRoot 'D:\Private\Venice' -Latest
$DiagStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
.\venice.ps1 diagnostics -ArchiveRoot 'D:\Private\Venice' -Latest -Output "D:\Share\Venice-Diagnostics-$DiagStamp"
```

To verify one named run, use `-Run 'D:\Private\Venice\RUN-NAME-firefox'`. Verification reads and hashes the snapshot, export, and ZIP without changing the run.

After verification, copy the complete immutable run—including `snapshot/`, not only `readable/` or the ZIP—to separate offline or access-controlled storage off the computer. Verify the copied snapshot manifest before treating that second copy as durable. Keep Firefox and Brave evidence independently; neither is a reason to overwrite or discard the other.

`diagnostics` produces content-free JSON and Markdown intended for a browser vendor or issue report. It includes store counts, wrapper-depth distributions, encrypted outer-record byte-size statistics, schema/wire compatibility, and error/warning/orphan totals. It excludes content, record IDs, keys, and local paths, but you should still review it before sharing. Byte-size statistics appear for captures made with a toolkit version that records them; older immutable snapshots can be re-extracted into a new run without modifying the original bytes. The output directory must not exist. Use a fresh timestamp as shown above; if it does exist, leave it intact and retry with the path suggested in the error.

## PASS contract

- Snapshot: every copied file matches the generated SHA-256 manifest.
- Inventory: a supported Venice storage backend was found and enumerated.
- Extraction: decryption authentication succeeded; errors, warnings, and orphans are empty.
- Rendering/media: readable chat files and referenced media are materialized consistently.
- Verification: stored counts, files, source labels, hashes, and encryption state agree.
- Lineage: the current and newest verified prior export were compared, then reproduced byte-for-byte.
- Packaging: ZIP CRC and SHA-256 checks succeed.

The tool selects Firefox from `profiles.ini` and Brave from `Local State`. When more than one Venice-bearing profile remains plausible, it stops and requires an override instead of guessing.
