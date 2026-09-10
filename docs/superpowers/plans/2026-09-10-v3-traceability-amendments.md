# Chat to How-To v3 Traceability Amendments

This file is normative for every P0-P3 task brief. It tightens acceptance
criteria where the phase plans use broader wording. The product specification
remains `chat_to_howto_codex_prompt_v3.md`.

## Cross-phase invariants

- Source health, documentation review, synthetic fixtures, and one platform's
  result can never set the other platform (or either real platform) to `ready`.
- All upstream adapters are read-only allowlists. They must have no generic
  request escape hatch and must reject upstream send, delete, SQL, AI, report,
  scheduled task, monitor, anti-recall, roaming-history, media-download, client
  exit/restart, login, and account-switch operations.
- Tests and reports distinguish four evidence classes: implemented code,
  automated synthetic tests, real-device verification, and user configuration
  still required.
- A single privacy scan covers stdout, stderr, application logs, persisted
  databases/artifacts, Git-tracked files, and user-visible generated artifacts.
  Fixture sentinels may appear only in an explicit test-fixture allowlist.
- New source facts update `docs/decisions.md`, the locked source contract, the
  affected tests, and `docs/acceptance-report.md` together.

## P0 additions

- Environment tests cover native ARM64 versus Rosetta translation without
  misreporting either as acceptance, and group undetectable fields into one
  actionable public issue without scanning chat/data directories.
- `docs/source-contracts.md` records for every candidate: official source and
  release channel, locked version/commit/hash or explicit unknown, license and
  notices, supported client/architecture matrix, access method and client
  impact, network/telemetry facts and unknowns, caches beyond the selected
  range, retention/cleanup, cancellation residue, and success/stop criteria.
- QCE tests keep media and roaming-history retrieval off and expose no method
  capable of closing/restarting QQ, logging in, scheduling exports, or writing
  upstream state. Record its ARM64 preview, copied/re-signed client, sandbox,
  binding, token-in-URL, and native-file retention constraints.
- TraceMemo uses an exact read-only endpoint allowlist. Tests reject send,
  report, agent, SQL, monitor, anti-recall, and related paths. The application
  has no WeChat DB-key extraction/memory-scanning interface and never configures
  a TraceMemo cloud-AI key.
- The confirmation card is tested field-by-field: source/license/version and
  limitations; access mechanism/client impact; possible over-range
  reads/caches; retention and cleanup; network/telemetry and how to disable it;
  success verification, refusal, failure, and safe stop behavior.
- Dukou remains a semi-automatic local-folder option only until stable ID,
  exact time range, pagination/completeness, local save, and cancellation are
  independently proven. Current original WeFlow capability deletion and the
  prohibition on relying on stale docs are recorded.
- `.env.example` has empty values only and no WeChat database-key field.
- P0 reporting emits independent QQ and WeChat rows. Client/tool/account
  changes invalidate prior verification. No backup/manual import is described
  as an automatic real-source export.

## P1 additions

- The main flow tests one or multiple conversations, duplicate-name
  disambiguation with stable IDs, complete-enumeration versus recent-list
  limitations, unknown-timezone refusal, editable seven-day default, and
  inclusive local dates converted to an exclusive instant boundary.
- Pre-export disclosure separately explains source-tool network activity and
  later model-content transfer. It names the exact selected scope, local output
  directory, possible upstream over-read/cache, completeness evidence, and
  cancellation residue.
- The manifest test asserts every v3 field: source/tool/client/account IDs and
  versions; stable conversation IDs and display names; requested and observed
  ranges; timezone; page/cursor/slice history; counts, earliest/latest,
  unknown-time and parse-error counts; completeness evidence and warnings;
  per-file hashes; schema version; and creation timestamp.
- First use requires an approved data root outside the repository, can remember
  that root only after approval, warns about iCloud/sync/backup risk, writes
  private atomic no-overwrite artifacts, and never silently substitutes the
  repository.
- Backup support claims only formats actually validated (such as the verified
  QQ TXT/ChatLab dialect). A WeChat backup is never treated as plain text by
  default. Malicious ZIP/Markdown/input fixtures stay inert.
- Complete filtering covers all available allowed-set text only; no miss may
  silently widen snapshot, conversation, account, or time scope.
- Export-job tests enumerate all v3 states, cover unknown totals without fake
  percentages, and include exactly-one-page, multi-page, same-second
  saturation, seconds/milliseconds, and cross-timezone cases.

## P2 additions

- The consent UI shows the actual redacted text payload, not statistics alone;
  tests prove the preview and final provider request are byte-for-byte derived
  from the same frozen payload and that every scope/payload/provider/model
  change invalidates consent.
- Generation-job tests enumerate all v3 states and distinguish a locally
  cancelled request from a provider request that may already have incurred
  cost. Usage includes every initial, repair, and retry request; price source,
  version/time, currency, estimate/actual status, and unknown price are stored
  and displayed without invention.
- The provider adapter has no implicit tools or browsing, uses one explicit
  provider SDK, defaults to unconfigured, uses only anonymous local IDs, and is
  live-tested only with separate user approval for key use and possible charge.

## P3 additions

- `docs/how-it-works.md` validates all sixteen v3 user actions, including first
  initialization and source disconnection. Each action names: user gesture,
  backend stages, local reads/writes, whether networking occurs and what is
  sent, failure/cancel residue, whether retry resumes or restarts, and whether
  cost may already exist. Every technical term is defined on first use.
- `docs/api-setup.md` states that a ChatGPT subscription is not API credit,
  that no purchase is automatic, and that existing keys are neither discovered
  nor used without operation-specific consent.
- `docs/privacy-and-cleanup.md` states SQLite is not encrypted by default,
  explains local filesystem permissions and iCloud/sync/backup exposure, and
  separates disconnect, delete indexed data, delete managed export artifacts,
  revoke consent, clear cache, and upstream-tool cleanup.
- Release verification records native ARM64 execution, loopback-only listeners,
  absence of public binding, and never presents non-Mac CI as Mac acceptance.
- Produce and verify a Python dependency lock in addition to `pnpm-lock.yaml`.
- Final documentation and acceptance report retain exact untested/blocked
  items, especially each real QQ/WeChat export and any live provider call.
