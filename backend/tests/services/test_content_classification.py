"""Body filtering must not promote screenshot chrome to imported content."""

from chat_to_howto.services.content_classification import classify_body
from chat_to_howto.services.image_ocr import OcrLine


def line(text: str, x: float, y: float, width: float = 0.7) -> OcrLine:
    return OcrLine(text=text, box=(x, y, width, 0.04), confidence=0.95)


def test_note_body_excludes_status_account_and_interaction_controls() -> None:
    page = classify_body((
        line("11:08", 0.05, 0.02, 0.16),
        line("某个账号", 0.22, 0.08, 0.3),
        line("关注", 0.72, 0.08, 0.12),
        line("自我介绍不用长篇大论", 0.05, 0.25),
        line("大学专业和实习经历应贴合岗位。", 0.05, 0.34),
        line("85 70 4", 0.48, 0.94, 0.3),
    ))
    assert page.kind == "note"
    assert [item.text for item in page.body_lines] == [
        "自我介绍不用长篇大论",
        "大学专业和实习经历应贴合岗位。",
    ]
    assert page.boundary_uncertain is False


def test_controls_only_has_no_importable_body() -> None:
    page = classify_body((
        line("11:08", 0.05, 0.02, 0.16),
        line("关注", 0.72, 0.08, 0.12),
        line("点赞 85", 0.46, 0.94, 0.2),
    ))
    assert page.kind == "uncertain"
    assert page.body_lines == ()


def test_edge_line_near_navigation_is_not_silently_imported() -> None:
    page = classify_body((line("可能被顶部导航遮挡的正文", 0.05, 0.10),))
    assert page.boundary_uncertain is True
    assert page.body_lines == ()


def test_long_line_touching_header_requires_correction_even_with_chrome_cue() -> None:
    page = classify_body((
        line("11:08", 0.05, 0.02, 0.16),
        line("这句正文紧贴顶部边界，需要用户核对。", 0.05, 0.13),
        line("正文下一段。", 0.05, 0.25),
    ))
    assert page.boundary_uncertain is True
    assert [item.text for item in page.body_lines] == ["正文下一段。"]


def test_cropped_body_at_top_is_not_silently_discarded() -> None:
    page = classify_body((line("正文从截图顶端开始", 0.05, 0.02),))
    assert page.boundary_uncertain is True
    assert page.body_lines == ()


def test_alternating_left_and_right_text_is_suggested_as_chat() -> None:
    page = classify_body((
        line("我们什么时候开始？", 0.05, 0.25, 0.4),
        line("明天上午。", 0.66, 0.36, 0.27),
        line("我会准备材料。", 0.05, 0.48, 0.4),
    ))
    assert page.kind == "chat"
    assert len(page.body_lines) == 3


def test_numbered_body_heading_is_not_mistaken_for_like_count() -> None:
    page = classify_body((
        line("1 自我介绍", 0.05, 0.28),
        line("重点说明与岗位匹配的经历。", 0.05, 0.36),
    ))
    assert [item.text for item in page.body_lines] == [
        "1 自我介绍", "重点说明与岗位匹配的经历。"
    ]
