# Locked source contracts (P0)

This document records public-source research and synthetic contract tests. It
does not represent a real-device export, tool installation, token entry, or
authorization to access chat data.

## QQ Chat Exporter (QCE)

- Locked release: `v6.2.10`, commit
  `cf8924d4790ddd857f5e26cca2b0163e4ec3b44f`. The annotated tag was unsigned.
- Candidate macOS arm64 preview asset: `NapCat-QCE-macOS-arm64-v6.2.10.tar.gz`,
  published SHA-256
  `c4156259a99232ff0fab9a59d90cd76e0581f75fde9ccab85f26d48096ef748e`.
  Neither the asset nor the signature was independently verified here.
- License: GPL-3.0. macOS arm64 is preview-only and creates, patches, and
  re-signs a private QQ runtime copy, links QQ data, and can require closing
  the normal client and logging into the copy. This conflicts with the current
  no-client-modification boundary until a user explicitly approves it.
- QCE listens on `0.0.0.0` by implementation, despite its local-use guidance.
  This application therefore accepts only a literal configured loopback target
  and a fixed port; it does not use `localhost`, redirects, or URL tokens.

The adapter is limited to `GET /health`, paged `GET /api/friends`, paged
`GET /api/groups`, bounded `GET /api/recent-contacts`, and
`POST /api/messages/fetch`. It uses `Authorization: Bearer` only; it never
places a token in a URL. Refresh, avatar/media retrieval, roaming history,
exports, tasks, login, restart/exit, and all writes are absent.

QCE filters are inclusive milliseconds. The adapter accepts increasing integer
millisecond `[start,end)` input, sends `endTime=end-1`, and filters messages
locally again. Pagination and same-timestamp pages are consumed without
timestamp-only deduplication. QCE lazy fetch, cache, and gap-repair behavior
cannot prove complete history, so every QCE fetch is `partial`; missing time or
identifier data adds uncertainty rather than fabricating success. Local
`cancel` prevents the next request only and never calls QCE task cancellation.

## TraceMemo

- Locked documentation/research point: desktop `v2.3.0`, shown at source point
  `4019051` (short SHA; the full commit, asset hash, signing, notarization, and
  dependency notices remain unverified).
- No repository license was found in the reviewed materials. Do not install,
  deploy, modify, or distribute it without a separate license decision.
- It documents macOS Apple Silicon support and only documents macOS WeChat
  `4.1.8.100` as fully tested. The observed/expected `4.1.13` combination is
  unverified. Automatic DB-key extraction requires SIP changes, administrator
  authorization, and process-memory access; this project never exposes or
  performs it. TraceMemo can create JSON caches and optional anti-recall data;
  anti-recall remains out of scope. Update checks and optional AI/Agent/share
  capabilities are separate network/retention risks and remain disabled by this
  adapter's allowlist.

The adapter permits only `GET /api/v1/health`, `GET /api/v1/contact`, and
`GET /api/v1/chatlog`. Health is unauthenticated and is never treated as source
readiness. The other two use an in-memory server-side Bearer token from the
scoped `SecretStore`; no token is returned, persisted here, placed in a URL, or
requested for health. Send, report, agent, SQL, monitor, anti-recall, media,
group snapshot, scheduled-report, token-management, and AI routes have no
adapter entry point.

TraceMemo takes Unix seconds and uses local-time semantics. The adapter
over-reads whole seconds around the requested integer-millisecond `[start,end)`
boundary, then applies the exact local range filter. Its HTTP `chatlog` route
has no pagination, limit, cursor, or completeness flag, so every successful
fetch remains `unknown`, including same-second saturation. Missing times/IDs
are not replaced or deduplicated by timestamp. Cancellation is local and does
not call any upstream cancellation/write endpoint.

## Shared transport and consent boundary

`LoopbackTransport` permits only literal `127.0.0.1` or `::1`, the configured
HTTP port, no userinfo/query/fragment/base path, no redirects, no proxy
environment, bounded timeouts, and streamed bounded bodies even when
`Content-Length` is absent or false. It emits typed body-free errors and does
not log response bodies, URLs, paths, conversation names, or secrets.

The existing `SecretAccessScope` binds source/account/client/tool/configuration
and secret operation, but not base URL, header choice, or range. This adapter
does not broaden that consent: it permits only the fixed header and fixed
loopback contracts above. Task 4 export consent must additionally bind the
selected operation and range before a real export; this code cannot establish
such approval or mark either source ready.

## Primary-source references and excluded candidates

QCE details are locked to `v6.2.10` route sources, including
`qq-chat-export-server/src/api/routes/messages.rs` and `friends.rs` at
<https://github.com/shuakami/qq-chat-exporter/tree/v6.2.10>. QCE may retain
partial output after local or upstream cancellation; its cache, lazy fetch,
and native-file cleanup are outside this adapter and must be disclosed at real
setup. TraceMemo route/contact facts come from
<https://github.com/Wxw-Gu/TraceMemo/blob/main/src/main/http-server.ts> and
`src/main/services/chat-service.ts`; its bootstrap cache, exports, Knowledge,
and recall archive require separate cleanup and are never controlled here.

Dukou `v0.1.11` is only a semi-automatic local-folder candidate: it lacks a
stable session ID, exact time range, and auditable pagination/completeness, and
can retain temporary records after cancellation. It is not an adapter fallback.
The original `hicccc77/WeFlow` repository removed key extraction, DB decryption,
and HTTP capability; stale forks or documentation are not treated as evidence.
