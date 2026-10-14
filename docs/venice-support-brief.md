# Venice support brief

Venice Local Archive is an unofficial, offline Windows toolkit for user-owned browser data. With Firefox and Brave closed, it makes a hash-verified local snapshot, authenticates and extracts known Venice records, renders a readable archive, validates counts and media, compares the capture with the newest verified prior run, and creates CRC-tested ZIPs with SHA-256 digests.

Its safety contract is append-only: output directories are unique, existing runs are never overwritten, failed evidence is retained, and comparisons do not merge or delete records. Capture code makes no network calls. The public repository contains source, synthetic tests, and sanitized documentation only; a scanner blocks browser data, exports, keys, personal paths, and unexpected binary artifacts.

The toolkit can generate a deliberately redacted support report with store counts, encrypted outer-record size statistics, nested-wrapper depth distributions, schema/wire-format versions, and problem totals. It excludes conversation content, record IDs, encryption keys, and local paths. Operators are instructed to preserve untouched raw snapshots on separate off-machine storage and to retain Firefox and Brave evidence independently.

The project does not modify Venice, browser storage, or account state and does not claim a supported import path. It is intended to help users preserve and inspect data they own and to make browser-storage format changes observable and reproducible.

## Boundaries

- This is a community tool published under its author's name. Venice does not endorse or support it.
- Operators use it on their own local encrypted data at their own risk.
- Raw Firefox and Brave evidence is preserved separately and should be copied to access-controlled off-machine storage.
- The toolkit records manifest schema version, per-store counts, snapshot checksums, archive hashes, unreadable-record warnings, encrypted record-size statistics, and wrapper-depth distributions.
- It performs no import, conflict resolution, account reset, or cloud restore. Venice cloud-backup scope and restore guarantees require authoritative product documentation from Venice.
