# P1 Export/Search Contract Supplement

> **Status:** normative P1 tracking supplement. Read with
> `chat_to_howto_codex_prompt_v3.md`,
> `docs/superpowers/plans/2026-09-10-v3-traceability-amendments.md`,
> `docs/source-contracts.md`, and
> `docs/superpowers/plans/2026-09-10-p1-export-search.md`.

## Purpose and boundary

This supplement resolves P1 contract gaps without changing locked P0 adapter
contracts. P1 exports user-selected, already-authorized conversations into
local structured artifacts, imports immutable snapshots, and provides local
rapid retrieval. It does not call a model, infer unseen attachment content,
create a guide, or publish a document.

P1 is a single-source, single-account batch. A cross-source or cross-account
selection is rejected before any fetch. Test fixtures are synthetic; no real
chat data, credentials, or media are used in development or tests.

## 1. Export scope and consent

P0's `ConsentScope` remains a single-conversation,
`operation="export_verification"` contract. It must not be widened or silently
reinterpreted for a P1 batch.

P1 introduces an immutable `ExportScope` which contains:

- `source_id`, `account_id`, and an ordered de-duplicated list of selected
  conversations (`conversation_id`, display name, and type when known);
- the requested UTC `[start, end)` interval, display timezone, destination
  root identity, and operation `"export"`;
- the observed client/tool/account version metadata and the exact selection
  configuration used for this run.

The UI presents the complete list of conversations, range, destination, and
known limitations before consent. The disclosure separately covers the source
tool's network activity, possible upstream over-read/cache, cancellation
residue, and the local directory. The export root is explicitly approved,
outside the repository, and warned for sync/iCloud/backup exposure before it
can be remembered. Consent is bound to a canonical,
versioned serialization (or fingerprint) of the full `ExportScope`; changing
a conversation, range, destination, source/account, or relevant configuration
requires fresh consent. The batch may expose human-readable display names, but
authorization and deduplication always use stable conversation IDs.

## 2. P0 adapter boundary

P1 obtains candidates through the locked P0 `list_conversations` contract and
fetches each selected stable ID only through its bounded `fetch_range` API.
`FetchPage` stays a materialized, bounded page with its
`CompletenessEvidence`; P1 must not replace it with a stream, unbounded
iterator, generic HTTP escape hatch, or broader adapter API. It also does not
perform source-side writes.

Every exported conversation retains its own evidence and warnings. P1 can add
batch orchestration around the calls, but it cannot upgrade an adapter's
completeness claim, manufacture missing message IDs/timestamps, or suppress a
fetch failure as a successful partial export. For an exact requested interval,
only records proven to be in `[start, end)` are saved; missing time is counted
and kept out of that precise result rather than substituted from export/import
time or file metadata.

## 3. Artifact schemas and atomic batch behavior

The final artifact directory is private (directory `0700`, files `0600`) and
is committed only after all selected conversation fetches and artifact writes
succeed. Write to a same-filesystem temporary directory, verify byte counts
and hashes, then atomically rename it into place. Cancellation, transport,
protocol, pagination, serialization, or writer failure leaves no committed
batch/snapshot and records no successful consent outcome. A successfully
committed batch that later fails only while building its local index remains a
valid artifact and may retry indexing without re-fetching.

### JSONL

JSONL is the canonical P1 message artifact: one normalized message record per
line, schema-versioned, UTF-8, and deterministic in conversation and source
order. A record includes batch/snapshot identity; source and account namespace;
stable conversation ID; required internal `message_id`; nullable
`source_message_id`; available source-file/page-or-slice provenance and source
order; UTC timestamp; sender; text; message kind; reply/reference ID; and
normalized attachment placeholders.

Unavailable source values are explicitly nullable (`null`) rather than
invented: in particular display name/type, sender fields, timestamp,
reply/reference ID, source-file/page-or-slice provenance, original filename,
and attachment metadata. Stable message IDs are mandatory for records that
enter the canonical snapshot, but
`source_message_id` is nullable. Deduplication first uses
source/account/conversation plus `source_message_id`. When that source ID is
absent, P1 derives a versioned internal `message_id` from conservative source
position: source/account/conversation plus the available
`source_file_id`, page/slice identity, `source_order`, and original timestamp.
It never deduplicates by text or position alone, so genuinely repeated identical
messages remain distinct. If the item has neither a source ID nor enough
source-position provenance to form that fallback, it is rejected or represented
only through an explicit, documented non-canonical error count.

An attachment placeholder may say only what metadata was observed, for example
`kind`, `original_name: null`, and `availability: "not_read"` or
`"metadata_only"`. It contains no local path, remote URL, bytes, extracted
text, OCR/ASR result, or inferred media description. Missing attachment
metadata is `null`/an empty list according to the schema, never an invented
file object.

### TXT and manifest

The P1 TXT artifact is a direct, readable rendering of exactly the selected
normalized text records. It is not a Markdown guide and does not add summaries
or content for unread media.

The manifest is schema-versioned and records: batch and artifact identities and
creation time; source, source-tool, client, and account IDs **and versions**;
selected stable conversation IDs and known display metadata; requested and
observed ranges plus timezone; page/cursor/slice history; per-conversation and
aggregate fetched/saved/duplicate/unknown-time/parse-error counts; earliest and
latest saved timestamps (or `null` for an empty set); exact
per-conversation `CompletenessEvidence` and warnings; artifact relative names,
byte sizes, and hashes. Manifest artifact hashes exclude the manifest itself to
avoid a circular digest. All absent metadata remains nullable.

## 4. Completeness and limitation acceptance

The manifest preserves evidence per conversation and derives batch
completeness conservatively: `unknown` dominates `partial`, `partial`
dominates `source_range_verified`, and `empty` is valid only when every
conversation is explicitly empty. A batch is `source_range_verified` only when
every selected conversation is verified. Any fetch/cancellation/protocol/budget
failure is a failed or cancelled batch, not a `partial` success.

`observed_range` is observational, never proof of full coverage. A committed
`partial` or `unknown` snapshot is not exposed to rapid retrieval until the
user explicitly accepts its displayed limitation. Acceptance is bound to the
batch ID plus manifest/evidence version or hash; a new export, changed
evidence, or changed scope invalidates it. `empty` may be displayed as an empty
result, but it is never promoted into evidence for guide generation.

## 5. Local rapid retrieval

P1 rapid retrieval operates only on a committed, consented immutable local
snapshot and returns bounded source context with its snapshot, conversation,
message, and evidence identifiers. It must not widen the export selection or
contact upstream systems.

Do not assume SQLite FTS5 is present or that its Chinese tokenization matches
the product requirement. P1's compatibility baseline is parameterized local
literal substring search (for example, escaped `LIKE` parameters) over
normalized text, tested with Chinese queries. User text never forms SQL,
patterns are escaped rather than treated as wildcards, and result ordering is
deterministic (timestamp where known, then stable conversation/source order).
FTS5 may become an opt-in, independently tested optimization only after its
availability and Chinese behavior are verified.

## 6. Backup-parser support declaration

Backup import is a manual secondary source, not an assertion that a proprietary
backup is complete or searchable. P1 may claim support only for fixtures and
formats with a reviewed mapping and acceptance tests (such as explicitly
supported CSV/JSON/JSONL and named QQ TXT/ChatLab dialects). It must provide a
preview/mapping confirmation before import.

WeChat backups have no default plain-text support claim: unsupported or opaque
backups are refused with an actionable limitation, not guessed or marked ready.
ZIP intake applies entry-count, compressed/decompressed-size, compression
ratio, traversal/absolute-path, symlink, and nested-archive limits before
reading content; it never executes extracted content.

## 7. Phase ownership

| Phase | Owns | Explicitly does not own |
| --- | --- | --- |
| P1 | Multi-conversation consent, bounded source export, JSONL/TXT/manifest, immutable import, completeness/limitation gating, local rapid retrieval, declared backup parsers | Model calls, complete filtering, guide or document generation |
| P2 | Complete filtering, model-enabled selection/analysis under a fresh narrow consent and provenance | Reinterpreting P1 evidence or broadening its snapshot |
| P3 | Editable/versioned Markdown and DOCX guide artifacts, editing and deletion lifecycle | Re-fetching sources or treating a guide as source completeness proof |

## Required acceptance matrix

| Area | Required test evidence |
| --- | --- |
| Scope/consent | One and multiple conversations, duplicate visible names with stable-ID disambiguation, cross-account rejection, editable local-date-to-UTC `[start,end)` conversion, unknown-timezone refusal, and invalidation when an ID/range/root changes; P0 `ConsentScope` and `FetchPage` signatures remain unchanged. |
| Export | Stable-ID duplicate suppression; nullable source fields; exact-range exclusion of unknown-time messages; attachment metadata-only placeholders; JSONL/TXT represent the same selected messages; no media path/bytes/text leaks. |
| Atomicity | A failure or cancellation in any selected conversation leaves no committed final batch; an index-only failure permits an index retry without a second fetch; manifest hashes and permissions are verified. |
| Evidence | Mixed verified/partial/unknown aggregation follows the conservative order; a transport/pagination failure is failure, not partial; partial/unknown search is blocked until hash-bound acceptance. |
| Search | Chinese literal queries, `%`/`_` escaping, SQL-injection-shaped input, deterministic ordering, selected-snapshot isolation, bounded context, and no upstream/model call. |
| Backup | Supported dialect preview/mapping tests; unsupported WeChat backup refusal; inert malformed/Markdown input; malicious ZIP count/size/ratio/path/symlink/nesting cases. |

Completion requires the matrix to pass with synthetic fixtures and the P1 plan
to link each implementation task to these contracts. Any desired new adapter
capability, FTS5 dependency, media read, model use, or document output is a
separate, reviewed contract change rather than an implicit P1 extension.
