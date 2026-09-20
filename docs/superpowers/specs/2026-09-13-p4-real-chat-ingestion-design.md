# P4 Real Chat Ingestion Design

## Scope

P4 adds a local, source-neutral import layer in front of the existing canonical
message, immutable snapshot, search, generation, evidence, and document flow.
It supports TXT, JSON, JSONL, CSV, XLSX, and pasted text. QQ/QCE and
WeChat/TraceMemo remain blocked and are not re-enabled.

The macOS Accessibility work is a separate proof-of-concept command. It is not
part of the production ingestion path, never bypasses normal Accessibility
authorization, and does not read visible message values unless a user invokes
an explicit content-reading option. This implementation will not run that
option against the user's real QQ or WeChat windows.

## Canonical data

Imports normalize into the existing `MessageInput` / `StoredMessage` schema.
`source_id` is `manual`; `platform` records `imported_file`, `paste`,
`qq_export`, or `wechat_export`. A SHA-256 fingerprint becomes the opaque
`source_file_id`; a source message ID is retained when supplied, otherwise
stable file position supplies identity. Original timestamps are retained and
converted to UTC only with an explicit timezone. Missing senders remain null.

Rows without a resolvable conversation or precise timestamp are reported as
unresolved and are not silently assigned guessed values. A user-supplied
default conversation name/identifier and default date are explicit inputs, not
inference. Unresolved row reports contain row numbers and reason codes, not raw
chat bodies.

## Parsing and preview

Parsing is deterministic and local. JSON/JSONL/CSV/XLSX use explicit column
mapping with conservative Chinese/English suggestions. TXT and paste support
only two documented dialects: a date-time/sender header followed by message
text, and a single-line `[HH:MM] sender: text` form. The latter requires an
explicit default date before it is importable.

The first request stores a bounded payload in a session-bound, expiring memory
cache and returns structural metadata. Analyze and commit requests re-read the
exact cached bytes. The preview includes format/origin, fingerprint, columns,
counts, conversations, time bounds, sender count, message-type counts, samples,
warnings, and bounded row issue codes. Commit requires explicit confirmation
and recomputes analysis before writing.

XLSX is parsed directly from bounded ZIP/XML members without extraction,
formula evaluation, macros, external conversion, or network access. Unsafe,
encrypted, corrupt, oversized, nested, or format-mismatched inputs fail closed.

## Persistence and authority

A successful commit atomically creates a manual import batch, stable canonical
messages, one sealed snapshot, one session-owned import record, and short-lived
opaque conversation handles in the existing chat SQLite database. Reimporting
identical bytes retains stable message IDs. Conflicting content for the same
identity is rejected instead of overwriting an existing message.

Search and generation snapshot authorization are extended to accept either an
existing completed searchable export job or a completed manual import owned by
the same local session. All downstream search, exclusion, preview, generation,
evidence, and document code remains unchanged.

## UI

An `Import chat` panel appears independently of the blocked QQ/WeChat setup.
The user chooses file or paste, optional QQ/WeChat provenance, explicit
timezone/default date/default conversation, and column mappings. The user must
review an analysis and confirm before commit. On success, the existing search
and generation workspace is reused in import mode with the returned snapshot,
conversation handles, and precise imported time range.

## Accessibility POC

The standalone macOS command uses AppKit and Accessibility APIs directly.
Default self-test operation only reports installed/running application metadata
and never creates a target AX element. Target mode requires both an exact
QQ/WeChat choice and explicit visible-content acknowledgement, then inspects
only the focused visible window without click, input, or scroll. It never reads
full `AXValue`, title, description, text-area, or text-field values; only a
bounded visible range of framed `StaticText` may be classified, and captured
strings are never printed. No memory access, key
extraction, injection, client modification, SIP changes, or hidden database
access is present.

## Verification

Tests cover every parser, format mismatch and bounds, mapping/time/unresolved
behavior, stable IDs and duplicate/conflict handling, session authority,
repository migration, full synthetic Import → Snapshot → Search →
Exclusion → Preview → FakeProvider → Evidence → Document, frontend
preview/confirm behavior, desktop/360px browser flow, and Accessibility result
classification with synthetic trees only.
