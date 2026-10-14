# Venice Local Archive

An unofficial, local-only preservation toolkit for data you own in Venice.ai. It captures closed Firefox and Brave profiles on Windows, creates immutable snapshots and readable exports, validates every layer, compares versions, and packages CRC-tested archives. It does not upload data or contact Venice.

## Offline operator checklist

1. Install 64-bit Python 3.12.
2. Clone or download this toolkit.
3. Run online setup with `.\setup.ps1`, or install from a downloaded wheelhouse with `.\setup.ps1 -Wheelhouse 'D:\Installers\venice-wheelhouse'`.
4. Choose a private archive directory outside this Git repository, such as `D:\Private\Venice`.
5. Close Firefox, Brave, and Tor Browser completely.
6. Run `.\venice.ps1 doctor -ArchiveRoot 'D:\Private\Venice'` and require `"status": "PASS"`.
7. Run `.\venice.ps1 backup -ArchiveRoot 'D:\Private\Venice' -Browser all`.
8. Open the reported `operator-summary.md`.
9. Require every checkpoint for both browsers to say `PASS`.
10. If anything fails, use the matching stage in [Troubleshooting](docs/troubleshooting.md), retain the incomplete run, and use its safe retry command.
11. Reopen browsers only after completion.

The backup command is offline. It snapshots before reading, never modifies a browser profile, never replaces an earlier run, and continues with the second browser if the first fails. `status` emits metadata and counts only:

Snapshot file counts describe physical browser-storage files, not conversations. Conversation and message counts describe recovered Venice records in that one capture; older immutable runs remain in the archive even when the newest profile contains fewer records. See the [count vocabulary](docs/format.md#count-vocabulary).

```powershell
.\venice.ps1 status -ArchiveRoot 'D:\Private\Venice'
.\venice.ps1 verify -ArchiveRoot 'D:\Private\Venice' -Latest
$DiagStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
.\venice.ps1 diagnostics -ArchiveRoot 'D:\Private\Venice' -Latest -Output "D:\Share\Venice-Diagnostics-$DiagStamp"
```

`diagnostics` creates a deliberately redacted support report containing physical record-size statistics, encryption-wrapper depth distributions, store names/counts, schema and wire-format versions, and problem counts. It excludes conversation content, record IDs, encryption keys, and local paths. Review the files before sharing them. Its output directory must be new; the timestamped example prevents collisions and the tool never overwrites an earlier report.

If discovery reports ambiguity, pass the profile explicitly:

```powershell
.\venice.ps1 backup -ArchiveRoot 'D:\Private\Venice' -Browser firefox `
  -FirefoxProfile 'D:\BrowserProfiles\Firefox\profile.default-release'
```

## What version comparison means

- Unchanged capture: the same logical ID and content hash is recorded as unchanged.
- Strict extension: the same ID retains the earlier ordered message prefix and adds later messages.
- Prefix: content resembles the start of another thread but does not prove identity; it is flagged for review.
- Branch or edit: shared history diverges or earlier content changes; both versions remain immutable.
- Different IDs: records remain separate even when their text is similar.

A newer complete version never overwrites earlier evidence. Comparison reports are deterministic and are immediately rerun with `--verify-existing`.

## Scope and safety

Public v0.1.0 supports Windows 10/11, Python 3.12, Firefox, and Brave. Other browsers are rejected by the operator interface. This is a readable preservation format, not a supported Venice import format. Keep the archive private: snapshots can contain encrypted and plaintext data, and readable exports intentionally contain conversation text and media.

Keep at least one untouched copy of every raw snapshot on separate storage off the computer. A verified readable export is not a substitute for the original browser bytes. Firefox and Brave runs remain independent; never discard one merely because the other captured successfully.

See the [operator guide](docs/operator-guide.md), [format notes](docs/format.md), [security policy](SECURITY.md), and [Venice support brief](docs/venice-support-brief.md).

Licensed under Apache-2.0. This project is not affiliated with Venice.ai.

This is a community tool, not an endorsed or supported Venice product. It operates on the operator's own local encrypted data at the operator's risk. It does not promise compatibility with future browser or Venice storage formats, does not describe Venice cloud-backup coverage or restore guarantees, and does not import data into Venice.
