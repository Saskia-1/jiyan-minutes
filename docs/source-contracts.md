# Locked source contracts (P0)

This is a record of public primary-source research, offline packaged-asset
static audit, and synthetic adapter contracts, **not** evidence of an
installation, device test, tool launch, token/key entry, or chat export. No
candidate is `ready` on the strength of this document. Both audited binaries
are currently NO-GO; a future replacement would have to pass its own gates
before a separately confirmed, user-selected narrow real-source check.

The platform adapter is read-only and has no generic upstream-request escape
hatch. It never sends messages, deletes upstream data, performs SQL, enables
AI/Agent/report/monitor/anti-recall/roaming/media-download functions, changes
accounts, or closes/restarts/logs into either client. Refusal, unsupported
versions, missing evidence, or a required boundary exception are safe stops;
they are not invitations to find a workaround.

## QQ Chat Exporter (QCE) — blocked after binary audit

**Source and supply chain.** The public author repository is
[`shuakami/qq-chat-exporter`](https://github.com/shuakami/qq-chat-exporter).
The candidate release channel is its GitHub release
[`v6.2.10`](https://github.com/shuakami/qq-chat-exporter/releases/tag/v6.2.10).
Its annotated tag resolves to commit
`cf8924d4790ddd857f5e26cca2b0163e4ec3b44f` (tag object
`6eac56ba049a20343ceb34f42d28dea5a2ab68a9`); these are the fixed source
references used below, not `master`. The macOS arm64 preview asset is
`NapCat-QCE-macOS-arm64-v6.2.10.tar.gz`; the release metadata publishes
size 41,657,553 bytes and SHA-256
`c4156259a99232ff0fab9a59d90cd76e0581f75fde9ccab85f26d48096ef748e`.
The official asset was downloaded to a private temporary audit directory; its
actual size and independently calculated SHA-256 match exactly. The GitHub
Release API reported `immutable: false`, and the annotated tag and target
commit are unsigned, so this fixes an audit baseline rather than proving an
immutable or authenticated release.

The unpacked archive contained 985 safely preflighted entries and four arm64
Mach-O files. `qce-server`, `napi2native`, and `MoeHoo` are only ad-hoc/linker
signed with no Team ID; `ffmpegAddon` is unsigned. `spctl` rejected
`qce-server`, and no Developer ID, notarization, sealed-resources, or other
Gatekeeper-acceptance evidence was obtained.
The launcher explicitly clears extended attributes with `xattr -cr`.
`napi2native` statically contains Frida Gum interception and Mach memory/thread
APIs, including send/recv replacement. The bundle has no product-level
`LICENSE`, `NOTICE`, `THIRD_PARTY`, SBOM, or lockfile and does not provide a
complete native-component attribution set. QCE source is GPL-3.0, while
bundled NapCat and UI text add separate/custom usage terms. These results make
the fixed binary **NO-GO** for installation or real-chat testing. Full evidence
and hashes are in [`ultra-gate-1-binary-audit.md`](ultra-gate-1-binary-audit.md).

**Client/architecture and access impact.** The documented macOS route is an
Apple Silicon/M1+ **preview** (no official Intel package in the reviewed
material). The locally installed QQ is `7.0.1` build `52892`, with a universal
x86_64/arm64 main executable and App Sandbox enabled. QCE's release gives a
generic minimum QQ build `34606` and recommends `9.9.19-34740`, but no upstream
evidence was found for the exact macOS combination QQ `7.0.1-52892` + QCE
`6.2.10`; numeric build comparison across these product version lines is not
compatibility proof. It requires QQ.app in `/Applications` or `~/Applications`, Xcode
CLI signing tools, and about 1.5 GB free space. It creates a roughly 1 GB
`QQNapCatRuntime.app` beside the unpacked tool by copying QQ.app, patching the
copy's `package.json` to load NapCat, and locally re-signing it. The copy
permanently loses App Sandbox and is linked to
`~/Library/Application Support/QQ/`; it does not directly change the original
QQ.app, but it changes the new runtime and QQ-data link state. Full mode
requires QQ to be fully quit, asks the user to scan in to the copy, consumes a
desktop-login slot, and reopening normal QQ can disconnect QCE. Standalone
view mode avoids QQ login but live friends, groups, message-fetch, and new
export functions return `503 STANDALONE_MODE`. These are material client
changes and conflict with the current no-client-modification boundary unless a
user specifically approves them; this document grants no such approval.
That approval model would apply only to a future candidate that has already
passed its supply-chain gate; it cannot unlock the audited v6.2.10 binary.
The audited launcher may also move a pre-existing link target to a
`.qce-unlinked-backup`. Its local bridge port-conflict handler finds a listener
with `lsof` and sends `SIGTERM` without first proving that the process belongs
to QCE. These are additional safe-stop conditions, not approved setup steps.

**Service and allowed adapter surface.** QCE defaults to port `40653` and its
implementation binds `0.0.0.0`, although its UI describes local use. Its own
security model is a 40-character token plus IP allowlist (default loopback,
seven-day expiry; Unix `security.json` mode `0600`); the project also accepts
query/cookie token forms and its UI can put a token in a URL. The platform
uses only a literal configured loopback origin and port, `Authorization:
Bearer`, no redirects, and never puts the token in a URL. Its allowlist is:

- `GET /health` (liveness only, never source readiness);
- paged `GET /api/friends` and `GET /api/groups`;
- bounded `GET /api/recent-contacts`; and
- `POST /api/messages/fetch`.

There is no adapter call for QCE exports/tasks/cancellation, refresh,
avatars/resources, media, roaming history, login, exit, restart, or writes.
QCE's fetch accepts inclusive millisecond times; the adapter accepts increasing
integer-millisecond `[start,end)`, sends `end - 1`, and filters locally again.
It preserves same-timestamp records rather than timestamp-only deduplicating.

The following is the synthetic adapter contract against the reviewed QCE
source. A field accepted by this adapter is not device-verification evidence.

| Surface | Exact fields and adapter interpretation |
| --- | --- |
| Envelope / health | All responses require `success: true` and object `data`. Health requires `data.status == "healthy"`, nonempty string `mode`, and boolean `online`; it still returns `ready=False`. |
| Friends | `data.friends[]`: `uid` is the stable peer identifier; optional string `nick` is the display name. Local ID is `friend:<uid>`. |
| Groups | `data.groups[]`: `groupCode` is the stable peer identifier; optional string `groupName` is the display name. Local ID is `group:<groupCode>`. |
| Recent contacts | `data.contacts[]`: `peerUid`, optional string `peerName`, and numeric non-boolean integer `chatType` of `1` (friend) or `2` (group). Request uses `includeAll: true`, and `limit` at most 100 within the remaining enumeration budget. |
| Fetch request | `peer: {chatType: 1 or 2, peerUid: <stable ID>}`; `filter: {startTime: start_ms, endTime: end_ms - 1}`; `batchSize: 5000`, `page` starting at 1, `limit: 2000`, `forceRefresh: false`. |
| Message record | `msgId` is a nonempty string or non-boolean integer normalized to string; `msgTime` is a non-boolean integer. Current normalization converts values below `100_000_000_000` from seconds to milliseconds and otherwise preserves milliseconds; this heuristic is not independent timestamp proof. Text concatenates `elements[].textElement.content` strings in order, ignoring non-text elements; malformed text elements reject that record. |
| Pagination | `currentPage` must be a non-boolean integer equal to the requested page; `hasNext` must be boolean. `friends`, `groups`, or `messages` must be lists no larger than the requested limit. Missing/invalid page evidence stops with a typed error. |

P0 limits are local guardrails for a narrow read, not upstream retention or
coverage guarantees. Message fetch permits at most 20 pages and 20,000 raw
entries per operation. Friend/group enumeration permits at most 10 pages per
collection, with a shared 10,000-raw-entry budget across friends, groups, and
recent contacts. Friends/groups request at most 2,000 entries per page. The
adapter reserves a full next page before issuing it, keeping that collection's
page size fixed so offsets do not shift; it can therefore stop below the raw
entry ceiling. Exceeding a response limit or requiring another request beyond
either budget raises `PaginationError`, with no partial-success `FetchPage`.
These checks count records before parsing, filtering, or deduplication.

**Range, cache, retention, and cancellation.** QCE may lazy-fetch more history
than the final range and caches message data for ten minutes (at most 64
entries, up to 20,000 messages each). Its cache/gap-repair and pagination
cannot prove complete history, so a QCE result is `partial`, never
`source_range_verified`, unless a later real check supplies separate evidence.
QCE may retain `security.json`, the copied runtime/link state, task outputs,
resource directories, and partial ZIP/JSONL/HTML files. QCE task cancellation
is cooperative and can leave started I/O; deleting a task is not a general
export-file cleanup, and `original-files` cleanup applies only where that task
has such a directory. The platform's local cancellation prevents further
upstream requests and checks again after a response and before returning a
fetch result, raising `FetchCancelled` instead of delivering a successful page.
It cannot interrupt an already issued synchronous HTTP request or undo tool
side effects, and it does not call QCE cancellation or any write endpoint.
Setup must disclose these residues and give the user the tool's own,
version-verified cleanup steps; none are performed by this adapter.

**Network/telemetry.** Static packaged-code inspection found GitHub release/
repository checks, npmmirror font loading, Vercel Analytics, CDN fallback,
plugin indexes, and proxy/mirror endpoints. QCE also talks to the local NapCat
bridge; enabling avatars/media can contact QQ/NapCat services, and avatar paths
use Tencent image domains. The adapter disables those optional paths by not
requesting avatars, resources, media, roaming, or export features, but cannot
disable the tool's whole network surface. No static evidence explicitly showed
chat-body exfiltration; opaque native modules and the lack of a runtime network
trace mean this is not proof of no exfiltration or telemetry. The standalone
launcher can print the local token and a tokenized URL, and the WebUI contains
a user-URL proxy surface, so the token must be treated as a broad sensitive
capability.

**Decision criteria.** The audited v6.2.10 asset is currently rejected before
installation. Re-evaluation first requires an authenticated, license-complete,
auditable release and explicit compatibility evidence for local QQ. A real
success would then require an approved QCE version/asset,
the disclosed client change, a user-selected account/conversation/narrow
range, saved normalized local files, and separately recorded range/count and
completeness evidence. Health, login, a cache hit, or documentation alone is
not success. For a future gate-passing candidate, refuse/safe-stop before
installation or connection if the user does not approve copied/re-signed QQ,
required logout/relogin, data linking, or the non-loopback upstream listener;
approval of those effects does not override the current binary NO-GO. Fail/stop on unavailable source,
invalid/expired token, standalone mode, account/client/version change,
pagination uncertainty, range mismatch, or cancellation; retain only the
explicitly disclosed local residue and never report a cancelled/partial read
as a complete export.

Fixed-source pointers: [commit tree](https://github.com/shuakami/qq-chat-exporter/tree/cf8924d4790ddd857f5e26cca2b0163e4ec3b44f),
[macOS guide](https://raw.githubusercontent.com/shuakami/qq-chat-exporter/cf8924d4790ddd857f5e26cca2b0163e4ec3b44f/docs/macos-deploy.md),
[server entry](https://raw.githubusercontent.com/shuakami/qq-chat-exporter/cf8924d4790ddd857f5e26cca2b0163e4ec3b44f/qq-chat-export-server/src/main.rs),
and [message routes](https://raw.githubusercontent.com/shuakami/qq-chat-exporter/cf8924d4790ddd857f5e26cca2b0163e4ec3b44f/qq-chat-export-server/src/api/routes/messages.rs).

## TraceMemo — blocked after binary audit

**Source/version status.** The public author repository is
[`Wxw-Gu/TraceMemo`](https://github.com/Wxw-Gu/TraceMemo); its public release
channel identifies [`v2.3.0`](https://github.com/Wxw-Gu/TraceMemo/releases/tag/v2.3.0)
as the candidate (published 2026-09-05). The lightweight tag resolves directly
to full commit `40190511fcad656ce439eece2201f1eb9a0d9352`; GitHub reports that
commit as unsigned. Release metadata publishes macOS arm64 ZIP size
226,870,607 bytes/SHA-256
`d8089eb4b0b6a486bfa9c55e44a0ad609bb8945bfe151bb83f02cfe352bd29a9`
and DMG size 234,566,020 bytes/SHA-256
`92ec7d0a4f4f0274227ec55e9608ef88f9d826b8033b7034d2c5a74ad3950460`.
The official DMG was downloaded to a private temporary audit directory; its
actual size and independently calculated SHA-256 match exactly, and
`hdiutil verify` reported valid CRCs. The tagged source `package.json` still
declares `2.2.3`, while the packaged `Info.plist` and ASAR package metadata say
`2.3.0`; the release/source discrepancy remains unexplained.
The GitHub Release API reports `immutable: false`; recording the tag commit
and published digests fixes this audit baseline but does not prove that the
remote release record or assets are immutable.

The fixed tree has no root standard `LICENSE`/`COPYING`/`NOTICE`, its
`package.json` has no license field, and GitHub's repository-license endpoint
returns 404. The README instead gives custom terms for personal learning,
modification/forking, and noncommercial use while restricting unauthorized
commercial use. Those terms do not establish a standard open-source license
or complete redistribution rights, and component-specific third-party
licenses do not license the whole application. Its packaged build
configuration sets `notarize: false`, enables
JIT, unsigned executable memory, and DYLD environment variables, and includes
an executable `resources/xkey_helper` blob (343,488 bytes; Git blob
`1c9b951830de80ef672d14a35bf28bb3a0eda49a`). The packaged helper has that same
Git blob identity, but this is an opaque binary correspondence, not source
provenance.

The audited DMG is unsigned. The App has no Developer ID or Team ID, lacks
sealed resources and Hardened Runtime, and fails deep strict signature
verification. Of 34 inspected Mach-O paths, 0 had a Team ID; 19 were invalidly
signed or unsigned. Both slices of `xkey_helper` and `libwx_key.dylib` report
invalid signatures. Gatekeeper did not accept the package and no valid stapled
notarization ticket was verified. ASAR header integrity matches its untrusted
`Info.plist` value, but that file is not bound to a trusted publisher signature.
The package has dependency-level license files but no product-level license,
SBOM, `THIRD_PARTY`, or complete native-component attribution. The fixed binary
is therefore **NO-GO** for installation or real-chat testing. Full evidence and
hashes are in [`ultra-gate-1-binary-audit.md`](ultra-gate-1-binary-audit.md).

**Supported client/architecture and key boundary.** Public materials say macOS
Apple Silicon/arm64 only, not Intel. The only fully tested macOS WeChat
combination reported is `4.1.8.100`; the locally installed WeChat `4.1.13`
is not listed as tested and is therefore unverified, not an upstream-declared
incompatibility. The packaged App declares macOS 12, but audited key/WCDB native
libraries declare a macOS 15 minimum; enterprise WeChat and actual device
behavior are also unverified. Automatic DB-key acquisition checks that SIP is
disabled,
then requests administrator authorization and scans the running WeChat
process's memory through `xkey_helper`. That path requires a separate,
high-risk system exception and is outside this project: the platform exposes
no DB-key extraction, memory scanning, SIP change, admin elevation, or
TraceMemo cloud-AI-key configuration. A manually supplied key could only be
considered for a future candidate that first passes the binary, license, and
compatibility gates; it does not make this audited v2.3.0 asset acceptable.
This platform never receives, persists, logs, or asks for that key.

The fixed source reads WeChat 4.x data below the selected account's
`xwechat_files/.../db_storage` tree and opens it through bundled native WCDB
components using a 64-hex-character database key. It stores a manually entered
per-account key through Electron `safeStorage` in a mode-`0600` file; on macOS
`safeStorage` uses Keychain-backed protection and may display a system prompt.
Neither this observation nor Tencent WCDB's public SQLite/SQLCipher heritage
proves the exact WeChat `4.1.13` schema, cipher parameters, or database shard
format. No verified importer for WeChat's official backup format was found.

**Service and read-only contract.** The reviewed packaged code defaults are a
loopback server at `127.0.0.1:6131`, enabled by default. `/api/v1/health` is
unauthenticated liveness only; it is never readiness evidence. The scoped
adapter can use only `GET /api/v1/contact` and `GET /api/v1/chatlog` with the
in-memory Bearer token supplied by the project's secret scope; it does not
return, persist, or URL-encode that token. It rejects every other route,
including send, report, agent, SQL, monitor, anti-recall, media,
group-snapshot, scheduled-report, token-management, and AI paths. The tool's
own token is a broad same-machine bearer capability, not a per-conversation or
time-scoped permission model: the same token also authorizes scheduled-task
writes/execution and `/agent/send`, and the tool can be configured for a
non-loopback host.

| Surface | Exact fields and adapter interpretation |
| --- | --- |
| Health | Top-level `ok: true` and boolean `ready` are required; no secret is read and local readiness remains false. There is no QCE-style `success/data` envelope. |
| Contact | Top-level `contacts[]`, each with `md5` matching exactly 32 hexadecimal characters, `type` exactly `user` or `group`, and optional string `m_nsNickName`. Normalize MD5 to lowercase without trimming; names are display-only. Only IDs from this adapter's fully successful latest enumeration may reach chatlog. |
| Chatlog request | `talker` is that enumerated MD5; `startTime = floor(start_ms / 1000)` and `endTime = ceil(end_ms / 1000)`. No verified HTTP pagination, cursor, offset, or limit is available. |
| Chatlog record | Top-level `messages[]`; `id` is a nonempty string or non-boolean integer normalized to string; `createTime` is Unix seconds as a non-boolean integer, multiplied by 1000. The current parser also accepts `datetime` only when `createTime` is absent; this compatibility fallback is not release verification. `content` is a string or absent/null. Invalid records are counted; usable in-range records remain in raw source order. |
| Declared count | Optional top-level `count`, if present, must be a non-boolean nonnegative integer; null, booleans, negatives, strings, and floats raise body-free `UpstreamProtocolError`. Compare it with raw `len(messages)`, not accepted rows. A difference adds fixed warning `upstream_count_mismatch`. The exact upstream declaration is not persisted in the current evidence schema; `fetched_count` and slice `returned` remain actual observed list lengths. Neither a matching count nor a zero count proves complete history. |

**Range/cache/cleanup/cancellation.** `chatlog` has no verified HTTP page,
cursor, offset, or limit and may return all matching local history; `contact`
also lacks a verified page/limit. The adapter over-reads whole Unix seconds
around a requested millisecond `[start,end)` and filters locally, but a
same-second saturation cannot be made complete by time slicing. Thus every
successful TraceMemo fetch is `unknown`, never complete. Reviewed tagged code
also creates user-data JSON bootstrap caches (contacts/account data, up to 768
message buckets × last 120 items, and up to 768 group snapshots). Optional
anti-recall is out of scope and must remain off: it can write WeChat DB journal
structures and keep per-session recall archives (up to 3,000 messages and 500
recalls); turning it off does not prove removal. Knowledge/index/export/report
history, media/voice cache, Agent Hub logs, exact paths, encryption, TTLs, and
cleanup coverage are not fully verified. Disconnecting this platform only
invalidates its access and unstarted work; it does not clear TraceMemo keys,
caches, DB changes, system permission, or backups. Local cancellation stops
new adapter work and makes no upstream cancellation/write call; an already
issued response, cache write, DB monitor, or prior tool residue may remain.
The adapter checks cancellation after receiving a response and before delivery;
cancelled work raises `FetchCancelled` and does not deliver a successful page.
P0 does not invent a server-side row limit or split unpaged TraceMemo responses
to claim safety or completeness. The configured transport applies a response
byte cap (default 1,048,576 bytes) and HTTP timeout (default 5 seconds per HTTPX
timeout phase, not a total-operation deadline). An over-limit response fails
without returning a successful page. The byte cap is a local response guard,
not a promise to bound upstream work or all intermediate decoding allocations.

**Network/telemetry and controls.** Packaged main-process code shows that first
launch is not a passive offline initialization. It defaults to the
`127.0.0.1:6131` API, unconditionally starts an Agent Hub at
`127.0.0.1:5300`, executes packaged `wechat-connector accounts --json`, may
start that connector at `127.0.0.1:18011` for an existing account, starts the
daily-report scheduler, and checks GitHub for updates after about 1.5 seconds.
macOS automatic update download/install is disabled, but the check is still
network activity. The connector statically includes QR login, text/media send,
media upload, inbound webhook, and local HTTP API capabilities. Remote AI,
Fish Audio, Hugging Face models, share-card upload, and remote avatar paths are
also present as optional external-transfer surfaces. The adapter neither
configures nor calls them, but cannot prevent the tool from starting its own
services or prove a tool-wide offline/telemetry-disable mode.

**Decision criteria.** The audited v2.3.0 asset is rejected before installation,
including when a user already lawfully possesses a database key. Re-evaluation
requires a Developer ID-signed/notarized and strictly verifiable immutable
artifact, explained/reproducible source-version correspondence, product license
and full native-component attribution, explicit WeChat 4.1.13 support, and a
way to disable update, Agent Hub, connector, scheduler and external-AI startup
surfaces. A future success would additionally require an approved arm64/WeChat
combination;
an already initialized route that does not require SIP/admin/memory scanning;
an approved narrow source read; and saved files whose range/count limitations
are recorded as `unknown`. Health 200, a token, or a contact list is not
success. Refuse/safe-stop on any SIP/admin/key-extraction request, missing
license/supply-chain proof, non-loopback service, client/account/version
change, token/DB-not-ready error, non-disambiguated conversation, no-page
response risk the user has not accepted, cancellation, or range mismatch.

Fixed candidate pointers:
[release](https://github.com/Wxw-Gu/TraceMemo/releases/tag/v2.3.0),
[commit tree](https://github.com/Wxw-Gu/TraceMemo/tree/40190511fcad656ce439eece2201f1eb9a0d9352),
[macOS build configuration](https://github.com/Wxw-Gu/TraceMemo/blob/40190511fcad656ce439eece2201f1eb9a0d9352/electron-builder.yml), and
[first-use requirements](https://github.com/Wxw-Gu/TraceMemo/blob/40190511fcad656ce439eece2201f1eb9a0d9352/docs/user-guide/getting-started.md).

## Normalized messages and completeness evidence

`SourceMessage` contains `message_id` (stable source ID or `None`),
`conversation_id`, `sent_at_ms`, `text`, and `source_order`. `source_order` is
the zero-based raw response position before filtering, parsing rejection, or
deduplication; QCE continues the index across pages, and TraceMemo uses its
single response. It is not a source sequence number or evidence of continuity.
Distinct IDs at the same timestamp are preserved. Repeated usable IDs are
deduplicated only after an occurrence is accepted, so an earlier out-of-range
or malformed occurrence does not hide a later usable one.

| Evidence | Meaning |
| --- | --- |
| `requested_start_ms`, `requested_end_ms` | The caller's increasing integer-millisecond half-open interval `[start,end)`. |
| `observed_start_ms`, `observed_end_ms` | Minimum and maximum accepted timestamps, both inclusive; `None` for no accepted rows. These are observations, not verified range endpoints. |
| `slices` | One entry per response. `page`/`cursor` are the values sent; `requested` is the requested row limit; `returned` counts all raw list entries; `accepted` counts retained rows from that response; `has_next` records upstream pagination evidence. Unavailable fields are `None`, including TraceMemo page/cursor/limit/has-next. |
| `fetched_count`, `accepted_count` | Actual raw entries received across slices versus normalized rows retained after parsing, range filtering, and ID deduplication. They never substitute a declared upstream count. |
| `duplicate_count` | Repeated usable IDs among all raw records, even when an occurrence is rejected. |
| `unknown_time_count`, `missing_id_count`, `parse_error_count` | Rows with unusable timestamps, unusable IDs, or malformed record/text structure. Counters can overlap. Non-object rows increment parse errors; other counters need parsed fields. Unmeasured counters remain `None`, not manufactured zero. |
| `warnings` / `reason` | Fixed warning codes and controlled explanatory text; no upstream body, token, key, or personal-data interpolation. Current warning codes include source-specific history uncertainty, `second_precision_boundaries`, `upstream_count_mismatch`, `duplicate_ids`, `unknown_timestamps`, `missing_identifiers`, and `malformed_records`. |
| `termination` / `next_cursor` | QCE success records `upstream_exhausted`; TraceMemo records `no_upstream_pagination_or_completeness`. Both return `next_cursor=None` after adapter processing. This is no further adapter cursor, not proof that all source history was obtained. Budget, protocol, transport, and cancellation failures raise instead of returning successful evidence. |

The status vocabulary has four distinct meanings:

- `source_range_verified`: independent evidence establishes source coverage of
  the requested range. Neither candidate adapter ever assigns this status on
  its own, even with health success, empty responses, matching counts, or
  exhausted pagination.
- `partial`: the returned data has known coverage limitations. QCE always uses
  this status because lazy history retrieval, cache, and gap repair leave
  coverage unproven; zero accepted rows do not remove those limitations.
- `unknown`: available evidence cannot determine source-range completeness.
  TraceMemo always uses this status, including empty results and matching
  declared counts, because no pagination/completeness signal is verified.
- `empty`: an evidence producer explicitly reports no accepted messages. This
  status alone does not establish that the source range is empty or verified.
  Neither candidate uses it to override its `partial`/`unknown` limitations.

## Dukou — semi-automatic local-folder alternative, not an adapter fallback

**Source/version/license.** The public author repository is
[`qzz0518/Dukou`](https://github.com/qzz0518/Dukou); its release channel shows
[`v0.1.11`](https://github.com/qzz0518/Dukou/releases/tag/v0.1.11), dated
2026-09-09, with short SHA `08da47c`. The full commit, asset SHA-256,
signing/notarization, dependency notices, and runtime audit are unresolved;
the reviewed current `main` implementation is not proof of a frozen release
asset. The repository declares MIT, while packaged-asset and third-party
notices still require inspection before distribution.

**Capability/access boundary.** Dukou is a macOS Accessibility-driven WeChat
native merged-forward automation, not a stable database/API reader. It uses a
user-entered/Accessibility-observed chat name rather than a stable session ID,
accepts only “most recent N messages” in its executable automation path, and
has no verified exact `[start,end)`, cursor/page, deduplication, continuity, or
complete-history proof. It needs user-granted Accessibility; it does not have
reviewed evidence of DB-key extraction or SIP adjustment. Exact macOS,
Apple-Silicon, and WeChat-version support is unverified. It can cancel and
save a generated ZIP to a user-selected local folder, but cancellation leaves
already received files in a staging shelf. Therefore it may be offered only
as a disclosed, user-operated local-folder option, never as the P0 automatic
precise-range adapter or evidence of a verified export.

**External delivery, retention, and stop criteria.** Selecting ChatGPT/Codex,
Claude, clipboard, or a custom app writes the ZIP (and optional prompt/path)
to the system pasteboard, foregrounds the target, and simulates paste. That
is delivery to another app; its cloud upload and retention are outside Dukou's
control. The only acceptable default is an explicit local-folder delivery;
all app/clipboard targets must remain unselected. The README reports App
Group ZIP retention and records moving to Trash after seven days. Its declared
network boundary is updates (Sparkle dependency); exact update-disable control,
telemetry inventory, and runtime network trace are unverified. A local-folder
success is only “ZIP saved”; it cannot satisfy stable-ID, exact-range,
pagination, or completeness success. Refuse/safe-stop if Accessibility is
not approved, a nonlocal delivery target is selected, or precise-range/
completeness is requested. On cancellation/failure, disclose and let the user
clean the staging/App Group/Trash residue; the platform does not delete it.

Public references: [release](https://github.com/qzz0518/Dukou/releases/tag/v0.1.11)
and [repository](https://github.com/qzz0518/Dukou). A release-immutable source
tree and full commit are unresolved and are intentionally not fabricated.

## WeFlow (original author) — excluded

**Identity and source status.** Only the original-author repository
[`hicccc77/WeFlow`](https://github.com/hicccc77/WeFlow) is considered here.
At the reviewed current `main` state it contains README/image material and
states that key extraction, DB decryption, and software interference were
removed; it describes a UI shell that accepts user-supplied unencrypted SQLite.
Its [releases](https://github.com/hicccc77/WeFlow/releases) and
[tags](https://github.com/hicccc77/WeFlow/tags) provide no candidate release or
tag. Thus version, full commit/hash, asset channel, signature/notarization,
license/notices, architecture/client matrix, service contract, telemetry,
retention/cleanup, and cancellation behavior are unresolved. It must not be
installed, connected, or treated as a P0 source candidate.

There is no verified current official HTTP/database-reader capability to
allowlist. Old documentation, search snippets, or the separate
`jacklilyhello/WeFlow` repository cannot be attributed to the original author;
that separate project would require a new source, license, supply-chain,
access, network, retention, and cancellation assessment. Safe-stop is simply
to make no WeFlow operation. It has no success criterion for this platform;
the correct current outcome is `unsupported`/`blocked`, not a manual-import or
stale-document substitute.
