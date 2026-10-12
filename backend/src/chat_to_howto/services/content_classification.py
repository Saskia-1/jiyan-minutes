"""Local-only screenshot type suggestion and body extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from chat_to_howto.services.image_ocr import OcrLine


@dataclass(frozen=True, slots=True)
class ClassifiedPage:
    kind: Literal["chat", "note", "uncertain"]
    body_lines: tuple[OcrLine, ...]
    boundary_uncertain: bool


def classify_body(lines: tuple[OcrLine, ...]) -> ClassifiedPage:
    ordered = sorted(lines, key=lambda line: (line.box[1], line.box[0]))
    # Chrome markers only set geometry boundaries; they are never content.
    top_chrome = any(
        line.box[1] < 0.15 and (
            line.box[1] < 0.07 and bool(re.fullmatch(r"\d{1,2}:\d{2}", line.text.strip()))
            or line.text.strip() in {"关注", "返回", "分享"}
        )
        for line in ordered
    )
    body: list[OcrLine] = []
    boundary_uncertain = False
    for line in ordered:
        text = line.text.strip()
        if not text:
            continue
        top = line.box[1]
        if top < 0.15:
            if not top_chrome or (top >= 0.12 and len(text) >= 12):
                boundary_uncertain = True
            continue
        if top >= 0.90:
            if not (re.fullmatch(r"[\d\s]+", text) or re.match(r"^(点赞|收藏|评论|分享|说点什么)", text)):
                boundary_uncertain = True
            continue
        body.append(line)
    if not body:
        kind: Literal["chat", "note", "uncertain"] = "uncertain"
    elif len(body) >= 2 and any(item.box[0] < 0.2 for item in body) and any(item.box[0] >= 0.55 for item in body):
        kind = "chat"
    elif len(body) >= 2:
        kind = "note"
    else:
        kind = "uncertain"
    return ClassifiedPage(kind=kind, body_lines=tuple(body), boundary_uncertain=boundary_uncertain)
