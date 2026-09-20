"""Deterministic local normalization of user-supplied chat records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from chat_to_howto.domain.imports import (
    BackupRecord,
    ImportAnalysis,
    ImportConversationSummary,
    ImportIssue,
    ImportMessageTypeCounts,
    ImportOptions,
    ImportOrigin,
    ImportPlatformHint,
    ImportSample,
)
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.services.manual_import import BackupParser


_MAPPING_FIELDS = frozenset(BackupRecord.model_fields)
_ALIASES: dict[str, tuple[str, ...]] = {
    "conversation_id": ("conversationid", "conversation", "chatid", "会话id", "聊天id", "群id"),
    "conversation_name": ("conversationname", "chatname", "groupname", "会话", "会话名称", "聊天名称", "群名"),
    "sent_at": ("sentat", "timestamp", "time", "datetime", "date", "时间", "日期时间", "发送时间"),
    "sender_id": ("senderid", "userid", "发送者id", "用户id"),
    "sender_name": ("sendername", "sender", "nickname", "name", "发送者", "昵称", "姓名"),
    "message_type": ("messagetype", "type", "消息类型", "类型"),
    "text": ("text", "textcontent", "content", "body", "message", "内容", "消息内容", "文本"),
    "source_message_id": ("sourcemessageid", "messageid", "msgid", "id", "消息id"),
    "reply_to_message_id": ("replytomessageid", "replyto", "回复消息id"),
    "conversation_type": ("conversationtype", "chattype", "会话类型"),
}
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_MONTH_DAY_TIME = re.compile(
    r"^(?P<month>\d{2})-(?P<day>\d{2}) "
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})$"
)
_ISO_DATETIME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T]"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?P<fraction>\.\d{1,6})?)?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})?$"
)
_SAFE_ID = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_MAX_IMPORT_CONVERSATIONS = 500
_MAX_PREVIEW_ISSUES = 200


@dataclass(frozen=True, slots=True)
class AnalyzedImport:
    view: ImportAnalysis
    messages: tuple[StoredMessage, ...]
    fingerprint: str
    mapping: dict[str, str]


def aggregate_conversation_metadata(
    messages: tuple[StoredMessage, ...] | list[StoredMessage],
) -> tuple[dict[str, tuple[str | None, str | None]], bool]:
    """Keep the first known name/type and flag conflicting known values."""
    result: dict[str, tuple[str | None, str | None]] = {}
    conflict = False
    for message in messages:
        current_name, current_type = result.get(message.conversation_id, (None, None))
        if (
            current_name is not None
            and message.conversation_name is not None
            and current_name != message.conversation_name
        ) or (
            current_type is not None
            and message.conversation_type is not None
            and current_type != message.conversation_type
        ):
            conflict = True
        result[message.conversation_id] = (
            current_name or message.conversation_name,
            current_type or message.conversation_type,
        )
    return result, conflict


def _normalized_label(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value).casefold()


def suggested_mapping(columns: tuple[str, ...]) -> dict[str, str]:
    indexed = {_normalized_label(column): column for column in columns}
    result: dict[str, str] = {}
    for target, aliases in _ALIASES.items():
        source = next((indexed[alias] for alias in aliases if alias in indexed), None)
        if source is not None:
            result[target] = source
    return result


def _scalar(row: Mapping[str, object], column: str | None) -> str | None:
    if column is None:
        return None
    value = row.get(column)
    if value is None:
        return None
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("mapped import values must be scalar")
    normalized = str(value).strip()
    return normalized or None


def _parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("import timezone is invalid") from None


def _parse_default_date(value: str | None) -> date | None:
    if value is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("default import date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("default import date must use YYYY-MM-DD") from None


def _parse_timestamp(
    raw: str | None, *, zone: ZoneInfo, default_date: date | None,
    default_year: int | None,
) -> tuple[datetime | None, bool]:
    if raw is None:
        return None, False
    candidate = raw
    if _TIME_ONLY.fullmatch(candidate):
        if default_date is None:
            return None, False
        candidate = f"{default_date.isoformat()} {candidate}"
    month_day = _MONTH_DAY_TIME.fullmatch(candidate)
    if month_day is not None:
        if default_year is None:
            raise ValueError("default_year is required for MM-DD timestamps")
        candidate = (
            f'{default_year:04d}-{month_day.group("month")}-{month_day.group("day")} '
            f'{month_day.group("hour")}:{month_day.group("minute")}:{month_day.group("second")}'
        )
    parsed: datetime | None = None
    iso_match = _ISO_DATETIME.fullmatch(candidate)
    if iso_match is not None:
        normalized = (
            f'{iso_match.group("date")}T{int(iso_match.group("hour")):02d}:'
            f'{iso_match.group("minute")}:{iso_match.group("second") or "00"}'
            f'{iso_match.group("fraction") or ""}{iso_match.group("offset") or ""}'
        )
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None, False
    elif re.fullmatch(r"\d+(?:\.\d+)?", candidate):
        serial = float(candidate)
        if 1 <= serial < 2_958_466 and not serial.is_integer():
            parsed = datetime(1899, 12, 30) + timedelta(days=serial)
            localized = _localize_unambiguous(parsed, zone)
            return (
                localized.astimezone(timezone.utc) if localized is not None else None,
                localized is not None,
            )
    else:
        for format_string in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(candidate, format_string)
                break
            except ValueError:
                continue
    if parsed is None:
        return None, False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = _localize_unambiguous(parsed, zone)
        if parsed is None:
            return None, False
    return parsed.astimezone(timezone.utc), False


def _localize_unambiguous(value: datetime, zone: ZoneInfo) -> datetime | None:
    """Reject local wall times that do not identify exactly one real instant."""
    instants: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(timezone.utc)
        round_trip = instant.astimezone(zone).replace(tzinfo=None)
        if round_trip == value:
            instants[instant] = candidate
    if len(instants) != 1:
        return None
    return next(iter(instants.values()))


def _conversation_id(
    explicit: str | None,
    name: str | None,
    *,
    default_id: str | None,
    fingerprint: str,
) -> tuple[str | None, bool]:
    value = explicit or default_id
    if value is not None:
        if not _SAFE_ID.fullmatch(value):
            return None, False
        namespaced = sha256(f"{fingerprint}|id|{value}".encode("utf-8")).hexdigest()
        return f"conversation:{namespaced}", False
    if name is None:
        return None, False
    derived = sha256(f"{fingerprint}|{name}".encode("utf-8")).hexdigest()
    return f"conversation:{derived}", True


def _message_type(raw: str | None, text: str | None) -> tuple[str, str]:
    normalized = (raw or "").casefold()
    if normalized in {"text", "文本", "message"} or (not normalized and text is not None):
        return "text", "text"
    if normalized in {"image", "photo", "图片", "照片"}:
        return "image", "image"
    if normalized in {"file", "document", "文件"}:
        return "file", "file"
    if normalized:
        return raw or "other", "other"
    return "unknown", "unknown"


class ChatImportAnalyzer:
    def __init__(self, parser: BackupParser | None = None) -> None:
        self._parser = parser or BackupParser()

    def analyze(
        self,
        *,
        filename: str,
        payload: bytes,
        origin: ImportOrigin,
        platform_hint: ImportPlatformHint,
        options: ImportOptions,
    ) -> AnalyzedImport:
        preview = self._parser.preview(filename=filename, payload=payload)
        rows = self._parser.rows(filename=filename, payload=payload)
        mapping = dict(options.mapping) or suggested_mapping(preview.columns)
        if not set(mapping).issubset(_MAPPING_FIELDS):
            raise ValueError("import mapping contains an unknown target field")
        if any(source not in preview.columns for source in mapping.values()):
            raise ValueError("import mapping references an unknown source column")
        zone = _parse_timezone(options.timezone)
        default_date = _parse_default_date(options.default_date)
        fingerprint = sha256(payload).hexdigest()
        interpretation = json.dumps(
            {
                "v": 1,
                "source_fingerprint": fingerprint,
                "origin": origin,
                "platform_hint": platform_hint,
                "timezone": options.timezone,
                "default_date": options.default_date,
                "default_year": options.default_year,
                "default_conversation_id": options.default_conversation_id,
                "default_conversation_name": options.default_conversation_name,
                "mapping": mapping,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        interpretation_fingerprint = sha256(interpretation.encode("utf-8")).hexdigest()
        file_id = f'{"paste" if origin == "paste" else "import-file"}:{fingerprint}'
        account_id = f"import:{interpretation_fingerprint[:24]}"
        platform = (
            "qq_export" if platform_hint == "qq" else
            "wechat_export" if platform_hint == "wechat" else
            "paste" if origin == "paste" else "imported_file"
        )
        issues: list[ImportIssue] = []
        unresolved_rows = 0
        messages: list[StoredMessage] = []
        messages_by_id: dict[str, StoredMessage] = {}
        row_numbers: dict[str, int] = {}
        duplicates = 0
        warnings = ["manual_backup_is_secondary", "completeness_is_not_inferred"]
        used_excel_serial = False
        derived_conversation = False
        sender_unknown = False

        def record_issue(row_number: int, code: str) -> None:
            if len(issues) < _MAX_PREVIEW_ISSUES:
                issues.append(ImportIssue(row_number=row_number, code=code))  # type: ignore[arg-type]

        for row_number, row in enumerate(rows, 1):
            conversation_name = _scalar(row, mapping.get("conversation_name")) or options.default_conversation_name
            conversation, derived = _conversation_id(
                _scalar(row, mapping.get("conversation_id")), conversation_name,
                default_id=options.default_conversation_id,
                fingerprint=interpretation_fingerprint,
            )
            sent_raw = _scalar(row, mapping.get("sent_at"))
            sent_at, excel_serial = _parse_timestamp(
                sent_raw, zone=zone, default_date=default_date,
                default_year=options.default_year,
            )
            row_invalid = False
            if conversation is None:
                record_issue(row_number, "conversation_unresolved")
                row_invalid = True
            if sent_at is None:
                record_issue(row_number, "timestamp_unresolved")
                row_invalid = True
            if row_invalid:
                unresolved_rows += 1
                continue
            text = _scalar(row, mapping.get("text"))
            sender_name = _scalar(row, mapping.get("sender_name"))
            sender_id = _scalar(row, mapping.get("sender_id"))
            source_message_id = _scalar(row, mapping.get("source_message_id"))
            message_type, _bucket = _message_type(_scalar(row, mapping.get("message_type")), text)
            try:
                message = StoredMessage.from_input(MessageInput(
                    source_id="manual",
                    account_id=account_id,
                    conversation_id=conversation,
                    source_message_id=source_message_id,
                    source_file_id=file_id,
                    page_slice_id=None,
                    source_order=None if source_message_id is not None else row_number,
                    platform=platform,
                    conversation_name=conversation_name,
                    conversation_type=_scalar(row, mapping.get("conversation_type")),
                    sent_at_utc=sent_at,
                    original_timestamp=sent_raw,
                    source_timezone=options.timezone,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    message_type=message_type,
                    text=text,
                    reply_to_message_id=_scalar(row, mapping.get("reply_to_message_id")),
                ))
            except (ValidationError, ValueError):
                record_issue(row_number, "message_invalid")
                unresolved_rows += 1
                continue
            existing = messages_by_id.get(message.message_id)
            if existing is not None:
                if existing == message:
                    duplicates += 1
                else:
                    record_issue(row_number, "message_identity_conflict")
                    unresolved_rows += 1
                continue
            messages.append(message)
            messages_by_id[message.message_id] = message
            row_numbers[message.message_id] = row_number
            used_excel_serial = used_excel_serial or excel_serial
            derived_conversation = derived_conversation or derived
            sender_unknown = sender_unknown or (sender_name is None and sender_id is None)

        if issues:
            warnings.append("unresolved_rows_not_imported")
        if used_excel_serial:
            warnings.append("excel_serial_timestamp")
        if derived_conversation:
            warnings.append("conversation_id_derived_from_name")
        if sender_unknown:
            warnings.append("sender_unknown")

        timestamps = tuple(message.sent_at_utc for message in messages if message.sent_at_utc is not None)
        senders = {
            message.sender_id or message.sender_name
            for message in messages
            if message.sender_id or message.sender_name
        }
        counts = {key: 0 for key in ("text", "image", "file", "other", "unknown")}
        conversation_metadata, metadata_conflict = aggregate_conversation_metadata(messages)
        if metadata_conflict:
            warnings.append("conversation_metadata_conflict")
        conversation_counts: dict[str, int] = {}
        for message in messages:
            _type, bucket = _message_type(message.message_type, message.text)
            counts[bucket] += 1
            conversation_counts[message.conversation_id] = (
                conversation_counts.get(message.conversation_id, 0) + 1
            )
            if len(conversation_counts) > _MAX_IMPORT_CONVERSATIONS:
                raise ValueError("manual import conversation limit exceeded")
        samples = tuple(
            ImportSample(
                row_number=row_numbers[message.message_id],
                conversation_name=message.conversation_name,
                sender_name=message.sender_name,
                timestamp=message.sent_at_utc,
                message_type=message.message_type or "unknown",
                text=message.text[:500] if message.text is not None else None,
            )
            for message in messages[:20]
            if message.sent_at_utc is not None
        )
        view = ImportAnalysis(
            total_count=len(rows),
            parsed_count=len(messages),
            unresolved_count=unresolved_rows,
            duplicate_count=duplicates,
            sender_count=len(senders),
            time_start=min(timestamps) if timestamps else None,
            time_end=max(timestamps) if timestamps else None,
            message_types=ImportMessageTypeCounts(**counts),
            conversations=tuple(
                ImportConversationSummary(
                    conversation_id=conversation_id,
                    conversation_name=conversation_metadata[conversation_id][0],
                    message_count=count,
                )
                for conversation_id, count in sorted(conversation_counts.items())
            ),
            warnings=tuple(warnings),
            issues=tuple(issues),
            samples=samples,
        )
        return AnalyzedImport(view=view, messages=tuple(messages), fingerprint=fingerprint, mapping=mapping)
