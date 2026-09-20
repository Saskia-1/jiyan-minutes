"""Canonical manual-import analysis using synthetic local bytes only."""

from datetime import datetime, timezone

import pytest


def _service():
    from chat_to_howto.services.chat_imports import ChatImportAnalyzer

    return ChatImportAnalyzer()


def test_csv_mapping_normalizes_to_existing_canonical_message_schema() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = (
        "会话,时间,发送者,内容,消息ID\n"
        "项目群,2026-09-13 10:20,张三,今天下午开会,m-1\n"
        "项目群,2026-09-13 10:21,,几点？,m-2\n"
    ).encode()
    options = ImportOptions(
        mapping={
            "conversation_name": "会话",
            "sent_at": "时间",
            "sender_name": "发送者",
            "text": "内容",
            "source_message_id": "消息ID",
        },
        timezone="Asia/Shanghai",
    )

    analyzed = _service().analyze(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="qq", options=options,
    )

    assert analyzed.view.total_count == 2
    assert analyzed.view.parsed_count == 2
    assert analyzed.view.unresolved_count == 0
    assert analyzed.view.sender_count == 1
    assert analyzed.view.time_start == datetime(2026, 9, 13, 2, 20, tzinfo=timezone.utc)
    assert analyzed.view.time_end == datetime(2026, 9, 13, 2, 21, tzinfo=timezone.utc)
    assert analyzed.messages[0].source_id == "manual"
    assert analyzed.messages[0].platform == "qq_export"
    assert analyzed.messages[0].source_file_id.startswith("import-file:")
    assert analyzed.messages[0].conversation_name == "项目群"
    assert analyzed.messages[0].conversation_id.startswith("conversation:")
    assert analyzed.messages[0].sender_name == "张三"
    assert analyzed.messages[1].sender_name is None
    assert "sender_unknown" in analyzed.view.warnings


def test_inline_paste_requires_explicit_default_date_and_conversation() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = "[10:20] 张三：今天下午开会\n[10:21] 李四：几点？\n".encode()
    missing = _service().analyze(
        filename="pasted-chat.txt", payload=payload, origin="paste",
        platform_hint="generic", options=ImportOptions(timezone="Asia/Shanghai"),
    )
    ready = _service().analyze(
        filename="pasted-chat.txt", payload=payload, origin="paste",
        platform_hint="generic",
        options=ImportOptions(
            timezone="Asia/Shanghai", default_date="2026-09-13",
            default_conversation_name="项目群",
        ),
    )

    assert missing.view.parsed_count == 0
    assert missing.view.unresolved_count == 2
    assert {issue.code for issue in missing.view.issues} == {
        "conversation_unresolved", "timestamp_unresolved",
    }
    assert ready.view.parsed_count == 2
    assert ready.messages[0].platform == "paste"
    assert ready.messages[0].sent_at_utc == datetime(2026, 9, 13, 2, 20, tzinfo=timezone.utc)


def test_month_day_txt_requires_explicit_default_year_and_never_guesses() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = "Alice: 09-09 21:59:54\nhello\n".encode()
    common = {
        "filename": "chat.txt", "payload": payload, "origin": "imported_file",
        "platform_hint": "generic",
    }

    with pytest.raises(ValueError, match="default_year"):
        _service().analyze(
            **common,
            options=ImportOptions(
                timezone="Asia/Shanghai", default_conversation_name="cs发展路径",
            ),
        )

    analyzed = _service().analyze(
        **common,
        options=ImportOptions(
            timezone="Asia/Shanghai", default_year=2026,
            default_conversation_name="cs发展路径",
        ),
    )

    assert analyzed.view.parsed_count == 1
    assert analyzed.view.unresolved_count == 0
    assert analyzed.messages[0].sender_name == "Alice"
    assert analyzed.messages[0].text == "hello"
    assert analyzed.messages[0].original_timestamp == "09-09 21:59:54"
    assert analyzed.messages[0].sent_at_utc == datetime(2026, 9, 9, 13, 59, 54, tzinfo=timezone.utc)


def test_conversation_summary_keeps_first_known_metadata_from_later_rows() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = (
        "conversation,name,type,time,text\n"
        "c1,,,2026-09-13 10:20,first\n"
        "c1,项目群,group,2026-09-13 10:21,second\n"
    ).encode()
    analyzed = _service().analyze(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic",
        options=ImportOptions(
            mapping={
                "conversation_id": "conversation", "conversation_name": "name",
                "conversation_type": "type", "sent_at": "time", "text": "text",
            },
            timezone="Asia/Shanghai",
        ),
    )

    assert analyzed.view.conversations[0].conversation_name == "项目群"


def test_analysis_reports_invalid_rows_without_guessing_or_echoing_raw_content() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = (
        "conversation,time,sender,text\n"
        "c1,not-a-time,A,private-first\n"
        ",2026-09-13 10:21,B,private-second\n"
    ).encode()
    analyzed = _service().analyze(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic",
        options=ImportOptions(
            mapping={"conversation_id": "conversation", "sent_at": "time", "sender_name": "sender", "text": "text"},
            timezone="UTC",
        ),
    )

    assert analyzed.view.parsed_count == 0
    assert analyzed.view.unresolved_count == 2
    assert [(item.row_number, item.code) for item in analyzed.view.issues] == [
        (1, "timestamp_unresolved"), (2, "conversation_unresolved"),
    ]
    assert "private-first" not in analyzed.view.model_dump_json()
    assert "private-second" not in analyzed.view.model_dump_json()


def test_date_only_and_ambiguous_local_times_remain_unresolved() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = (
        "conversation,time,text\n"
        "c1,2026-09-13,date only\n"
        "c1,2026-W37-7,week date\n"
        "c1,2026-11-01 01:30,ambiguous DST\n"
        "c1,2026-03-08 02:30,missing DST\n"
    ).encode()
    analyzed = _service().analyze(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic",
        options=ImportOptions(
            mapping={"conversation_id": "conversation", "sent_at": "time", "text": "text"},
            timezone="America/New_York",
        ),
    )

    assert analyzed.view.parsed_count == 0
    assert analyzed.view.unresolved_count == 4
    assert [issue.code for issue in analyzed.view.issues] == ["timestamp_unresolved"] * 4


@pytest.mark.parametrize("default_date", ["20260913", "2026-W37-7"])
def test_default_date_requires_exact_calendar_date_syntax(default_date: str) -> None:
    from chat_to_howto.domain.imports import ImportOptions

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _service().analyze(
            filename="chat.txt", payload=b"[10:20] A:step\n", origin="paste",
            platform_hint="generic",
            options=ImportOptions(
                timezone="UTC", default_date=default_date,
                default_conversation_name="chat",
            ),
        )


def test_message_ids_are_stable_and_duplicate_or_conflicting_source_ids_are_bounded() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    options = ImportOptions(
        mapping={
            "conversation_id": "conversation", "sent_at": "time",
            "source_message_id": "id", "text": "text",
        },
        timezone="UTC",
    )
    duplicate_payload = (
        "conversation,time,id,text\n"
        "c1,2026-09-13 10:20,m1,same\n"
        "c1,2026-09-13 10:20,m1,same\n"
    ).encode()
    conflict_payload = duplicate_payload.replace(b",same\n", b",same\n", 1).replace(
        b"m1,same\n", b"m1,changed\n", 1
    )

    first = _service().analyze(
        filename="chat.csv", payload=duplicate_payload, origin="imported_file",
        platform_hint="generic", options=options,
    )
    repeated = _service().analyze(
        filename="chat.csv", payload=duplicate_payload, origin="imported_file",
        platform_hint="generic", options=options,
    )
    conflict = _service().analyze(
        filename="chat.csv", payload=conflict_payload, origin="imported_file",
        platform_hint="generic", options=options,
    )

    assert first.view.parsed_count == 1
    assert first.view.duplicate_count == 1
    assert first.messages[0].message_id == repeated.messages[0].message_id
    assert conflict.view.parsed_count == 1
    assert conflict.view.unresolved_count == 1
    assert conflict.view.issues[0].code == "message_identity_conflict"


def test_explicit_conversation_ids_are_namespaced_to_the_import_fingerprint() -> None:
    """Selecting one manual import cannot match a same-named ID from another import."""
    from chat_to_howto.domain.imports import ImportOptions

    options = ImportOptions(
        mapping={"conversation_id": "conversation", "sent_at": "time", "text": "text"},
        timezone="UTC",
    )
    first = _service().analyze(
        filename="first.csv",
        payload=b"conversation,time,text\nc1,2026-09-13 10:20,first\n",
        origin="imported_file", platform_hint="generic", options=options,
    )
    second = _service().analyze(
        filename="second.csv",
        payload=b"conversation,time,text\nc1,2026-09-13 10:20,second\n",
        origin="imported_file", platform_hint="generic", options=options,
    )

    assert first.messages[0].conversation_id != second.messages[0].conversation_id
    assert first.messages[0].conversation_id.startswith("conversation:")


def test_same_file_with_different_interpretation_keeps_provenance_without_identity_conflict() -> None:
    """Re-importing the bytes with different approved options is a distinct interpretation."""
    from chat_to_howto.domain.imports import ImportOptions

    payload = b"conversation,time,id,text\nc1,2026-09-13 10:20,m1,step\n"
    mapping = {
        "conversation_id": "conversation", "sent_at": "time",
        "source_message_id": "id", "text": "text",
    }
    utc = _service().analyze(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic", options=ImportOptions(mapping=mapping, timezone="UTC"),
    )
    shanghai = _service().analyze(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic",
        options=ImportOptions(mapping=mapping, timezone="Asia/Shanghai"),
    )

    assert utc.fingerprint == shanghai.fingerprint
    assert utc.messages[0].source_file_id == shanghai.messages[0].source_file_id
    assert utc.messages[0].account_id != shanghai.messages[0].account_id
    assert utc.messages[0].conversation_id != shanghai.messages[0].conversation_id
    assert utc.messages[0].message_id != shanghai.messages[0].message_id


def test_invalid_row_count_is_exact_while_preview_issues_are_capped() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = ("conversation,time,text\n" + "".join(
        f"c1,not-a-time,row-{index}\n" for index in range(250)
    )).encode()
    analyzed = _service().analyze(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
    )

    assert analyzed.view.unresolved_count == 250
    assert len(analyzed.view.issues) == 200
    assert analyzed.view.issues[-1].row_number == 200


def test_preview_rejects_more_conversations_than_commit_can_authorize() -> None:
    from chat_to_howto.domain.imports import ImportOptions

    payload = ("conversation,time,text\n" + "".join(
        f"c-{index},2026-09-13 10:20,row-{index}\n" for index in range(501)
    )).encode()

    with pytest.raises(ValueError, match="conversation limit"):
        _service().analyze(
            filename="chat.csv", payload=payload, origin="imported_file",
            platform_hint="generic", options=ImportOptions(timezone="UTC"),
        )
