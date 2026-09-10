# Locked source contracts (P0)

This is a record of public primary-source research and synthetic adapter
contracts, **not** evidence of an installation, device test, tool launch,
token/key entry, or chat export. No candidate is `ready` on the strength of
this document. A concrete setup confirmation and a separately recorded,
user-selected narrow real-source check are required before that status changes.

The platform adapter is read-only and has no generic upstream-request escape
hatch. It never sends messages, deletes upstream data, performs SQL, enables
AI/Agent/report/monitor/anti-recall/roaming/media-download functions, changes
accounts, or closes/restarts/logs into either client. Refusal, unsupported
versions, missing evidence, or a required boundary exception are safe stops;
they are not invitations to find a workaround.

## QQ Chat Exporter (QCE) — candidate only

**Source and supply chain.** The public author repository is
[`shuakami/qq-chat-exporter`](https://github.com/shuakami/qq-chat-exporter).
The candidate release channel is its GitHub release
[`v6.2.10`](https://github.com/shuakami/qq-chat-exporter/releases/tag/v6.2.10).
Its annotated tag resolves to commit
`cf8924d4790ddd857f5e26cca2b0163e4ec3b44f` (tag object
`6eac56ba049a20343ceb34f42d28dea5a2ab68a9`); these are the fixed source
references used below, not `master`. The macOS arm64 preview asset is
`NapCat-QCE-macOS-arm64-v6.2.10.tar.gz`; the release metadata publishes
SHA-256 `c4156259a99232ff0fab9a59d90cd76e0581f75fde9ccab85f26d48096ef748e`.
It has not been downloaded or independently hashed here, and the annotated
tag is reported unsigned. The source license is GPL-3.0; distribution or
modification obligations, third-party notices, QQ/NapCat terms, and the
release asset's notices have not been independently audited.

**Client/architecture and access impact.** The documented macOS route is an
Apple Silicon/M1+ **preview** (no official Intel package in the reviewed
material). It requires QQ.app in `/Applications` or `~/Applications`, Xcode
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

**Range, cache, retention, and cancellation.** QCE may lazy-fetch more history
than the final range and caches message data for ten minutes (at most 64
entries, up to 20,000 messages each). Its cache/gap-repair and pagination
cannot prove complete history, so a QCE result is `partial`, never
`source_range_verified`, unless a later real check supplies separate evidence.
QCE may retain `security.json`, the copied runtime/link state, task outputs,
resource directories, and partial ZIP/JSONL/HTML files. QCE task cancellation
is cooperative and can leave started I/O; deleting a task is not a general
export-file cleanup, and `original-files` cleanup applies only where that task
has such a directory. The platform's local cancellation only prevents the next
upstream request; it deliberately does not call QCE cancellation or any write
endpoint. Setup must disclose these residues and give the user the tool's own,
version-verified cleanup steps; none are performed by this adapter.

**Network/telemetry.** The reviewed project claims local read/parse and no
project server transfer, but that is not a binary or dependency network audit.
QCE talks to the local NapCat bridge; enabling avatars/media can contact QQ/
NapCat services, and avatar paths use Tencent image domains. The adapter
disables those paths by not requesting avatars, resources, media, roaming, or
export features. No complete telemetry/DNS/third-party-component inventory,
actual runtime request trace, or guaranteed update-disable mechanism was
verified; it must be stated as unknown, not "no telemetry".

**Decision criteria.** A real success requires an approved QCE version/asset,
the disclosed client change, a user-selected account/conversation/narrow
range, saved normalized local files, and separately recorded range/count and
completeness evidence. Health, login, a cache hit, or documentation alone is
not success. Refuse/safe-stop before installation or connection if the user
does not approve copied/re-signed QQ, required logout/relogin, data linking,
or the non-loopback upstream listener. Fail/stop on unavailable source,
invalid/expired token, standalone mode, account/client/version change,
pagination uncertainty, range mismatch, or cancellation; retain only the
explicitly disclosed local residue and never report a cancelled/partial read
as a complete export.

Fixed-source pointers: [tag tree](https://github.com/shuakami/qq-chat-exporter/tree/v6.2.10),
[macOS guide](https://raw.githubusercontent.com/shuakami/qq-chat-exporter/v6.2.10/docs/macos-deploy.md),
[server entry](https://raw.githubusercontent.com/shuakami/qq-chat-exporter/v6.2.10/qq-chat-export-server/src/main.rs),
and [message routes](https://raw.githubusercontent.com/shuakami/qq-chat-exporter/v6.2.10/qq-chat-export-server/src/api/routes/messages.rs).

## TraceMemo — blocked pending immutable supply-chain proof and consent

**Source/version status.** The public author repository is
[`Wxw-Gu/TraceMemo`](https://github.com/Wxw-Gu/TraceMemo); its public release
channel identifies [`v2.3.0`](https://github.com/Wxw-Gu/TraceMemo/releases/tag/v2.3.0)
as the candidate (release 2026-09-05; tag page date 2026-09-02). Public
material only exposed short SHA `4019051`. The full commit, annotated/signed
tag status, asset/DMG SHA-256, signing/notarization, SBOM, and dependency
notices are **unresolved**. Do not invent the remaining SHA or claim that the
release binary is locked. The detailed HTTP/cache/update observations were
read from the moving `main` source during research; they are evidence of that
review only, not proof that `v2.3.0` contains those exact routes. A
version-pinned code URL cannot be honestly supplied until the full commit/tag
contents are independently confirmed; treat the route-to-release association
as unresolved. The repository has no identified `LICENSE`/`COPYING`/`NOTICE`
or `package.json` license field in the reviewed material, so installation,
deployment, modification, or distribution is blocked pending written license
authority and third-party notices.

**Supported client/architecture and key boundary.** Public materials say macOS
Apple Silicon/arm64 only, not Intel. The only fully tested macOS WeChat
combination reported is `4.1.8.100`; WeChat `4.1.13` is explicitly unverified,
as are other 4.x compatibility, minimum macOS, enterprise WeChat, and actual
device behavior. Automatic DB-key acquisition checks that SIP is disabled,
then requests administrator authorization and scans the running WeChat
process's memory through `xkey_helper`. That path requires a separate,
high-risk system exception and is outside this project: the platform exposes
no DB-key extraction, memory scanning, SIP change, admin elevation, or
TraceMemo cloud-AI-key configuration. A manually supplied key may be a
TraceMemo alternative, but this platform does not receive, persist, log, or
ask for that key. If an already initialized tool cannot be used without those
actions, the source remains `blocked`.

**Service and read-only contract.** The reviewed main-source defaults are a
loopback server at `127.0.0.1:6131`, enabled by default. `/api/v1/health` is
unauthenticated liveness only; it is never readiness evidence. The scoped
adapter can use only `GET /api/v1/contact` and `GET /api/v1/chatlog` with the
in-memory Bearer token supplied by the project's secret scope; it does not
return, persist, or URL-encode that token. It rejects every other route,
including send, report, agent, SQL, monitor, anti-recall, media,
group-snapshot, scheduled-report, token-management, and AI paths. The tool's
own token is a broad same-machine bearer capability, not a per-conversation or
time-scoped permission model.

**Range/cache/cleanup/cancellation.** `chatlog` has no verified HTTP page,
cursor, offset, or limit and may return all matching local history; `contact`
also lacks a verified page/limit. The adapter over-reads whole Unix seconds
around a requested millisecond `[start,end)` and filters locally, but a
same-second saturation cannot be made complete by time slicing. Thus every
successful TraceMemo fetch is `unknown`, never complete. Reviewed main code
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

**Network/telemetry and controls.** Remote AI Search/daily report/image
features, Agent Hub, and experimental share-card infrastructure are separate
external-transfer paths when configured/used. The adapter neither configures
them nor calls them. Reviewed main code schedules an update check about 1.5
seconds after startup; macOS automatic download/install is disabled there, but
the check itself is network activity. The adapter cannot prove or enforce a
tool-wide update/telemetry disable switch. No standalone analytics/crash SDK
or complete runtime endpoint inventory was found in the limited review; that
does **not** establish zero telemetry. Any real setup must disclose and, where
the version actually supports it, disable API/AI/Agent/share/anti-recall and
nonessential update behavior before use; unknown controls are a stop condition
for an approved no-network requirement.

**Decision criteria.** A future success requires: written license/notice
authority; an independently fixed full release commit and asset integrity;
an approved arm64/WeChat combination (4.1.13 remains unverified until tested);
an already initialized route that does not require SIP/admin/memory scanning;
an approved narrow source read; and saved files whose range/count limitations
are recorded as `unknown`. Health 200, a token, or a contact list is not
success. Refuse/safe-stop on any SIP/admin/key-extraction request, missing
license/supply-chain proof, non-loopback service, client/account/version
change, token/DB-not-ready error, non-disambiguated conversation, no-page
response risk the user has not accepted, cancellation, or range mismatch.

Immutable candidate pointers (not proof of the unresolved full SHA):
[release](https://github.com/Wxw-Gu/TraceMemo/releases/tag/v2.3.0) and
[tag tree](https://github.com/Wxw-Gu/TraceMemo/tree/v2.3.0).

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
