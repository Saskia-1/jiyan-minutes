"""Bounded, non-executing parser for explicitly supported backup formats."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import PurePosixPath
import re
import stat
from typing import Mapping
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile, ZipInfo

from chat_to_howto.domain.imports import BackupPreview, BackupRecord


_TEXT_FORMATS = {".txt": "txt", ".csv": "csv", ".json": "json", ".jsonl": "jsonl"}
_FORMATS = {**_TEXT_FORMATS, ".xlsx": "xlsx"}
_MAPPING_FIELDS = frozenset(BackupRecord.model_fields)
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MAX_COLUMN_NAME_CHARS = 256


@dataclass(frozen=True, slots=True)
class BackupLimits:
    max_input_bytes: int = 64 * 1024 * 1024
    max_uncompressed_bytes: int = 128 * 1024 * 1024
    max_zip_entries: int = 256
    max_compression_ratio: float = 100.0
    max_rows: int = 1_000_000
    max_columns: int = 256
    max_field_chars: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if min(
            self.max_input_bytes,
            self.max_uncompressed_bytes,
            self.max_zip_entries,
            self.max_compression_ratio,
            self.max_rows,
            self.max_columns,
            self.max_field_chars,
        ) <= 0:
            raise ValueError("backup limits must be positive")


class BackupParser:
    """Preview and parse bytes without extracting, executing, fetching, or guessing."""

    def __init__(self, *, limits: BackupLimits | None = None) -> None:
        self._limits = limits or BackupLimits()

    def preview(self, *, filename: str, payload: bytes) -> BackupPreview:
        _validate_outer(filename, payload, self._limits)
        digest = sha256(payload).hexdigest()
        format_name, encoding, container, member_name, rows, columns = _read_table(
            filename, payload, self._limits
        )
        return BackupPreview(
            format=format_name,  # type: ignore[arg-type]
            encoding=encoding,  # type: ignore[arg-type]
            container=container,  # type: ignore[arg-type]
            member_name=member_name,
            row_count=len(rows),
            columns=columns,
            sha256=digest,
            byte_size=len(payload),
        )

    def rows(self, *, filename: str, payload: bytes) -> tuple[dict[str, object], ...]:
        """Return bounded inert row values for canonical analysis."""
        _validate_outer(filename, payload, self._limits)
        *_metadata, rows, _columns = _read_table(filename, payload, self._limits)
        return tuple(dict(row) for row in rows)

    def parse(
        self,
        *,
        filename: str,
        payload: bytes,
        expected_sha256: str,
        mapping: Mapping[str, str],
    ) -> tuple[BackupRecord, ...]:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or not _constant_equal(
            sha256(payload).hexdigest(), expected_sha256
        ):
            raise PermissionError("backup payload does not match the approved preview")
        preview = self.preview(filename=filename, payload=payload)
        rows = list(self.rows(filename=filename, payload=payload))
        normalized_mapping = _validate_mapping(mapping, preview.columns)
        records: list[BackupRecord] = []
        for index, row in enumerate(rows, start=1):
            values: dict[str, str | None] = {}
            for target, source in normalized_mapping.items():
                raw = row.get(source)
                if raw is None:
                    values[target] = None
                elif isinstance(raw, (str, int, float, bool)):
                    value = str(raw)
                    if len(value) > self._limits.max_field_chars:
                        raise ValueError(f"mapped field in row {index} exceeds the character limit")
                    values[target] = value
                else:
                    raise ValueError(f"mapped field in row {index} must be scalar")
            conversation = values.get("conversation_id")
            if not isinstance(conversation, str) or not conversation.strip():
                raise ValueError(f"conversation_id is empty in row {index}")
            values["conversation_id"] = conversation.strip()
            records.append(BackupRecord(**values))
        return tuple(records)


def _constant_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def _validate_outer(filename: str, payload: bytes, limits: BackupLimits) -> None:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("backup input is empty")
    if len(payload) > limits.max_input_bytes:
        raise ValueError("backup input exceeds the byte limit")
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 for character in filename)
    ):
        raise ValueError("backup filename must be a plain local name")


def _format_for(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("opaque WeChat/database backups are not supported as plain text")
    try:
        return _FORMATS[suffix]
    except KeyError as error:
        raise ValueError("unsupported or opaque backup format") from error


def _safe_zip_member(info: ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        raise ValueError("ZIP symbolic link entries are not allowed")
    if info.flag_bits & 0x1:
        raise ValueError("encrypted ZIP entries are not supported")
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError("ZIP entry path is unsafe")
    if path.suffix.lower() in {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}:
        raise ValueError("nested archive entries are not supported")


def _unwrap(
    filename: str, payload: bytes, limits: BackupLimits
) -> tuple[str, bytes, str, str | None]:
    if PurePosixPath(filename).suffix.lower() != ".zip":
        _format_for(filename)
        return filename, payload, "plain", None
    try:
        with ZipFile(BytesIO(payload), "r") as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_zip_entries:
                raise ValueError("ZIP entry count exceeds the configured limit")
            files: list[ZipInfo] = []
            total_uncompressed = 0
            total_compressed = 0
            for info in entries:
                _safe_zip_member(info)
                if info.is_dir():
                    continue
                files.append(info)
                total_uncompressed += info.file_size
                total_compressed += info.compress_size
                if info.file_size > limits.max_uncompressed_bytes:
                    raise ValueError("ZIP uncompressed size exceeds the configured limit")
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > limits.max_compression_ratio:
                    raise ValueError("ZIP compression ratio exceeds the configured limit")
            if total_uncompressed > limits.max_uncompressed_bytes:
                raise ValueError("ZIP uncompressed size exceeds the configured limit")
            if total_uncompressed / max(total_compressed, 1) > limits.max_compression_ratio:
                raise ValueError("ZIP compression ratio exceeds the configured limit")
            supported = [info for info in files if PurePosixPath(info.filename).suffix.lower() in _TEXT_FORMATS]
            if len(files) != 1 or len(supported) != 1:
                raise ValueError("ZIP must contain exactly one supported data file")
            info = supported[0]
            with archive.open(info, "r") as stream:
                content = stream.read(limits.max_uncompressed_bytes + 1)
            if len(content) != info.file_size or len(content) > limits.max_uncompressed_bytes:
                raise ValueError("ZIP member changed or exceeds the uncompressed limit")
            member_basename = PurePosixPath(info.filename).name
            return member_basename, content, "zip", member_basename
    except BadZipFile as error:
        raise ValueError("ZIP archive is corrupt") from error


def _decode(payload: bytes) -> tuple[str, str]:
    if payload.startswith(b"\xef\xbb\xbf"):
        try:
            return payload.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError as error:
            raise ValueError("backup encoding is invalid") from error
    for encoding in ("utf-8", "gb18030"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("backup encoding is neither valid UTF-8 nor GB18030")


def _parse_rows(
    format_name: str, text: str, limits: BackupLimits
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    if "\x00" in text:
        raise ValueError("backup text contains invalid null characters")
    if format_name == "txt":
        rows, columns = _parse_txt(text, limits)
    elif format_name == "csv":
        rows, columns = _parse_csv(text, limits)
    elif format_name == "json":
        rows, columns = _parse_json(text, limits)
    else:
        rows, columns = _parse_jsonl(text, limits)
    if len(rows) > limits.max_rows:
        raise ValueError("backup row limit exceeded")
    if len(columns) > limits.max_columns:
        raise ValueError("backup column limit exceeded")
    _validate_column_names(columns, limits)
    return rows, columns


def _validate_column_names(columns: tuple[str, ...], limits: BackupLimits) -> None:
    maximum = min(_MAX_COLUMN_NAME_CHARS, limits.max_field_chars)
    if any(len(column) > maximum for column in columns):
        raise ValueError("backup column name exceeds the character limit")


def _read_table(
    filename: str, payload: bytes, limits: BackupLimits
) -> tuple[str, str, str, str | None, list[dict[str, object]], tuple[str, ...]]:
    if PurePosixPath(filename).suffix.lower() == ".xlsx":
        rows, columns = _parse_xlsx(payload, limits)
        return "xlsx", "binary", "xlsx", None, rows, columns
    content_name, content, container, member_name = _unwrap(filename, payload, limits)
    format_name = _format_for(content_name)
    text, encoding = _decode(content)
    rows, columns = _parse_rows(format_name, text, limits)
    return format_name, encoding, container, member_name, rows, columns


_TXT_BLOCK_HEADER = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<sender>\S.*)$"
)
_TXT_INLINE = re.compile(
    r"^\[(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<sender>.+?)[：:]\s*(?P<text>.*)$"
)
_TXT_MONTH_DAY_HEADER = re.compile(
    r"^(?P<sender>.+):\s*(?P<date>\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})$"
)


def _parse_txt(
    text: str, limits: BackupLimits
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    lines = text.splitlines()
    nonblank = [(number, line) for number, line in enumerate(lines, 1) if line.strip()]
    if not nonblank:
        return [], ("sent_at", "sender_name", "text")
    inline_matches = [(number, _TXT_INLINE.fullmatch(line.strip())) for number, line in nonblank]
    if all(match is not None for _number, match in inline_matches):
        rows = [
            {
                "sent_at": match.group("time"),
                "sender_name": match.group("sender").strip(),
                "text": match.group("text").strip(),
            }
            for _number, match in inline_matches
            if match is not None
        ]
        _validate_text_rows(rows, limits)
        return rows, ("sent_at", "sender_name", "text")

    # Select this dialect only when the first nonblank line is a valid header.
    # This prevents a header-like line inside an existing documented TXT body
    # from changing the parser used for the whole file.
    month_day_dialect = _TXT_MONTH_DAY_HEADER.fullmatch(nonblank[0][1].strip()) is not None

    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    body: list[str] = []

    def finish() -> None:
        nonlocal current, body
        if current is None:
            return
        current["text"] = "\n".join(body).strip()
        rows.append(current)
        current = None
        body = []

    for line in lines:
        match = (
            _TXT_MONTH_DAY_HEADER.fullmatch(line.strip())
            if month_day_dialect
            else _TXT_BLOCK_HEADER.fullmatch(line.strip())
        )
        if match:
            finish()
            current = {
                "sent_at": f'{match.group("date")} {match.group("time")}',
                "sender_name": match.group("sender").strip(),
            }
        elif current is not None:
            body.append(line)
        elif line.strip():
            rows.append({"sent_at": "", "sender_name": "", "text": line.strip()})
    finish()
    _validate_text_rows(rows, limits)
    return rows, ("sent_at", "sender_name", "text")


def _validate_text_rows(rows: list[dict[str, object]], limits: BackupLimits) -> None:
    if len(rows) > limits.max_rows:
        raise ValueError("backup row limit exceeded")
    for row in rows:
        if any(isinstance(value, str) and len(value) > limits.max_field_chars for value in row.values()):
            raise ValueError("TXT field exceeds the character limit")


def _parse_xml(payload: bytes, label: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(payload)
    except (ElementTree.ParseError, RecursionError) as error:
        raise ValueError(f"XLSX {label} XML is invalid") from error


def _read_xlsx_members(payload: bytes, limits: BackupLimits) -> dict[str, bytes]:
    try:
        with ZipFile(BytesIO(payload), "r") as archive:
            entries = archive.infolist()
            if not entries or len(entries) > limits.max_zip_entries:
                raise ValueError("XLSX package entry count exceeds the configured limit")
            total_uncompressed = 0
            total_compressed = 0
            members: dict[str, bytes] = {}
            for info in entries:
                _safe_zip_member(info)
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                total_compressed += info.compress_size
                if info.file_size > limits.max_uncompressed_bytes:
                    raise ValueError("XLSX member exceeds the uncompressed limit")
                if info.file_size / max(info.compress_size, 1) > limits.max_compression_ratio:
                    raise ValueError("XLSX compression ratio exceeds the configured limit")
                if total_uncompressed > limits.max_uncompressed_bytes:
                    raise ValueError("XLSX package exceeds the uncompressed limit")
                with archive.open(info, "r") as stream:
                    value = stream.read(limits.max_uncompressed_bytes + 1)
                if len(value) != info.file_size:
                    raise ValueError("XLSX member changed while reading")
                members[info.filename] = value
            if total_uncompressed / max(total_compressed, 1) > limits.max_compression_ratio:
                raise ValueError("XLSX compression ratio exceeds the configured limit")
            return members
    except BadZipFile as error:
        raise ValueError("XLSX package is corrupt") from error


def _xlsx_sheet_name(members: Mapping[str, bytes]) -> str:
    workbook_payload = members.get("xl/workbook.xml")
    relationships_payload = members.get("xl/_rels/workbook.xml.rels")
    if workbook_payload is None or relationships_payload is None:
        raise ValueError("XLSX workbook package is incomplete")
    workbook = _parse_xml(workbook_payload, "workbook")
    relationships = _parse_xml(relationships_payload, "relationships")
    workbook_properties = workbook.find(f"{{{_SHEET_NS}}}workbookPr")
    if (
        workbook_properties is not None
        and workbook_properties.attrib.get("date1904", "0").casefold()
        in {"1", "true"}
    ):
        raise ValueError("XLSX 1904 date system is not supported safely")
    sheet = workbook.find(f".//{{{_SHEET_NS}}}sheet")
    if sheet is None:
        raise ValueError("XLSX workbook has no worksheet")
    relationship_id = sheet.attrib.get(f"{{{_DOCUMENT_REL_NS}}}id")
    targets = {
        item.attrib.get("Id"): item.attrib.get("Target")
        for item in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
        if item.attrib.get("Type", "").endswith("/worksheet")
    }
    target = targets.get(relationship_id)
    if not target or target.startswith("/") or "\\" in target:
        raise ValueError("XLSX worksheet relationship is invalid")
    path = PurePosixPath("xl") / target
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("XLSX worksheet relationship is unsafe")
    return str(path)


def _xlsx_shared_strings(members: Mapping[str, bytes], limits: BackupLimits) -> tuple[str, ...]:
    payload = members.get("xl/sharedStrings.xml")
    if payload is None:
        return ()
    root = _parse_xml(payload, "shared strings")
    values = tuple(
        "".join(node.text or "" for node in item.findall(f".//{{{_SHEET_NS}}}t"))
        for item in root.findall(f"{{{_SHEET_NS}}}si")
    )
    if any(len(value) > limits.max_field_chars for value in values):
        raise ValueError("XLSX shared string exceeds the character limit")
    return values


def _xlsx_column(reference: str, *, max_columns: int) -> int:
    # XLSX's physical domain is XFD/1,048,576. Bound the syntax before doing
    # arithmetic so a hostile, very long attribute cannot create giant ints.
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", reference)
    if match is None:
        raise ValueError("XLSX cell reference is invalid")
    if int(match.group(2)) > 1_048_576:
        raise ValueError("XLSX cell reference is invalid")
    index = 0
    for character in match.group(1):
        index = index * 26 + ord(character) - ord("A") + 1
        if index > max_columns:
            raise ValueError("backup column limit exceeded")
    return index - 1


def _xlsx_cell(cell: ElementTree.Element, shared: tuple[str, ...]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{{{_SHEET_NS}}}t"))
    value = cell.findtext(f"{{{_SHEET_NS}}}v", default="")
    if cell_type == "s":
        if not re.fullmatch(r"[0-9]{1,10}", value):
            raise ValueError("XLSX shared string reference is invalid") from None
        index = int(value)
        if index >= len(shared):
            raise ValueError("XLSX shared string reference is invalid")
        return shared[index]
    if cell_type == "b":
        if value not in {"0", "1"}:
            raise ValueError("XLSX boolean value is invalid")
        return "true" if value == "1" else "false"
    return value


def _parse_xlsx(
    payload: bytes, limits: BackupLimits
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    members = _read_xlsx_members(payload, limits)
    sheet_name = _xlsx_sheet_name(members)
    sheet_payload = members.get(sheet_name)
    if sheet_payload is None:
        raise ValueError("XLSX worksheet is missing")
    root = _parse_xml(sheet_payload, "worksheet")
    shared = _xlsx_shared_strings(members, limits)
    matrix: list[dict[int, str]] = []
    for row in root.findall(f".//{{{_SHEET_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{_SHEET_NS}}}c"):
            reference = cell.attrib.get("r", "")
            index = _xlsx_column(reference, max_columns=limits.max_columns)
            if index in values:
                raise ValueError("XLSX row contains a duplicate cell reference")
            value = _xlsx_cell(cell, shared)
            if len(value) > limits.max_field_chars:
                raise ValueError("XLSX cell exceeds the character limit")
            values[index] = value
        if values:
            matrix.append(values)
        if len(matrix) > limits.max_rows + 1:
            raise ValueError("backup row limit exceeded")
    if not matrix:
        raise ValueError("XLSX worksheet is empty")
    header_row = matrix[0]
    last_column = max(header_row)
    header = tuple(header_row.get(index, "").strip() for index in range(last_column + 1))
    if not header or any(not value for value in header):
        raise ValueError("XLSX columns must be named")
    if len(set(header)) != len(header):
        raise ValueError("XLSX contains duplicate column names")
    _validate_column_names(header, limits)
    rows = [
        {column: values.get(index, "") for index, column in enumerate(header)}
        for values in matrix[1:]
    ]
    return rows, header


def _parse_csv(text: str, limits: BackupLimits) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    try:
        reader = csv.reader(StringIO(text, newline=""), strict=True)
        header = next(reader)
    except (StopIteration, csv.Error) as error:
        raise ValueError("CSV header is missing or invalid") from error
    if not header or any(not column for column in header):
        raise ValueError("CSV columns must be named")
    if len(set(header)) != len(header):
        raise ValueError("CSV contains duplicate column names")
    if len(header) > limits.max_columns:
        raise ValueError("backup column limit exceeded")
    _validate_column_names(tuple(header), limits)
    rows: list[dict[str, object]] = []
    try:
        for values in reader:
            if len(values) != len(header):
                raise ValueError("CSV row has an inconsistent column count")
            if any(len(value) > limits.max_field_chars for value in values):
                raise ValueError("CSV field exceeds the character limit")
            rows.append(dict(zip(header, values, strict=True)))
            if len(rows) > limits.max_rows:
                raise ValueError("backup row limit exceeded")
    except csv.Error as error:
        raise ValueError("CSV content is invalid") from error
    return rows, tuple(header)


def _object_rows(values: object, limits: BackupLimits) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    if not isinstance(values, list):
        raise ValueError("JSON backup must contain an array of objects")
    rows: list[dict[str, object]] = []
    columns: list[str] = []
    for value in values:
        if not isinstance(value, dict) or not all(isinstance(key, str) and key for key in value):
            raise ValueError("JSON backup rows must be objects with named fields")
        _validate_column_names(tuple(value), limits)
        rows.append(value)
        for field_value in value.values():
            if isinstance(field_value, str) and len(field_value) > limits.max_field_chars:
                raise ValueError("JSON field exceeds the character limit")
        for key in value:
            if key not in columns:
                columns.append(key)
        if len(rows) > limits.max_rows:
            raise ValueError("backup row limit exceeded")
        if len(columns) > limits.max_columns:
            raise ValueError("backup column limit exceeded")
    return rows, tuple(columns)


def _parse_json(text: str, limits: BackupLimits) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    try:
        values = _loads_without_duplicate_keys(text)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError("JSON backup is invalid") from error
    return _object_rows(values, limits)


def _parse_jsonl(text: str, limits: BackupLimits) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    values: list[object] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"JSONL row {number} is empty")
        try:
            values.append(_loads_without_duplicate_keys(line))
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError(f"JSONL row {number} is invalid") from error
        if len(values) > limits.max_rows:
            raise ValueError("backup row limit exceeded")
    return _object_rows(values, limits)


def _loads_without_duplicate_keys(text: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON contains duplicate object keys")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique_object)


def _validate_mapping(
    mapping: Mapping[str, str], columns: tuple[str, ...]
) -> dict[str, str]:
    if not isinstance(mapping, Mapping) or "conversation_id" not in mapping:
        raise ValueError("mapping must include conversation_id")
    if not set(mapping).issubset(_MAPPING_FIELDS):
        raise ValueError("mapping contains an unknown target field")
    normalized: dict[str, str] = {}
    for target, source in mapping.items():
        if not isinstance(source, str) or source not in columns:
            raise ValueError("mapping references an unknown column")
        normalized[target] = source
    return normalized
