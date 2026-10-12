"""Local OCR boundary tests use synthetic images and a synthetic helper only."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import zlib

import pytest

from chat_to_howto.services.image_ocr import OcrError, recognize_image


def _png(width: int = 1, height: int = 1) -> bytes:
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height)) if width * height <= 100_000 else b""
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def _jpeg_header(width: int = 2, height: int = 3) -> bytes:
    sof = b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return b"\xff\xd8" + sof + b"\xff\xd9"


def _helper(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_rejects_unsupported_image_before_running_helper(tmp_path: Path) -> None:
    image = tmp_path / "not-image.png"
    image.write_bytes(b"not an image")
    with pytest.raises(OcrError) as caught:
        recognize_image(image, executable=tmp_path / "missing-helper", timeout_seconds=1)
    assert caught.value.code == "unsupported_image"


def test_rejects_decoded_image_above_forty_megapixels(tmp_path: Path) -> None:
    image = tmp_path / "large.png"
    image.write_bytes(_png(8000, 6000))
    with pytest.raises(OcrError) as caught:
        recognize_image(image, executable=tmp_path / "missing-helper", timeout_seconds=1)
    assert caught.value.code == "image_limit_exceeded"


def test_jpeg_dimensions_are_accepted_and_helper_lines_preserved(tmp_path: Path) -> None:
    image = tmp_path / "synthetic.jpg"
    image.write_bytes(_jpeg_header())
    helper = _helper(
        tmp_path / "ocr-helper",
        "import json\nprint(json.dumps({'lines': ["
        "{'text': '面试 tips', 'box': [0.1, 0.2, 0.8, 0.1], 'confidence': 0.9}"
        "]}, ensure_ascii=False))",
    )
    lines = recognize_image(image, executable=helper, timeout_seconds=2)
    assert [(line.text, line.box, line.confidence) for line in lines] == [
        ("面试 tips", (0.1, 0.2, 0.8, 0.1), 0.9)
    ]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("import sys; sys.exit(4)", "helper_failed"),
        ("print('not json')", "invalid_helper_output"),
        ("print('x' * 1048577)", "helper_output_limit"),
    ],
)
def test_helper_failure_is_bounded_and_content_free(
    tmp_path: Path, body: str, expected: str
) -> None:
    image = tmp_path / "synthetic.png"
    image.write_bytes(_png())
    helper = _helper(tmp_path / "ocr-helper", body)
    with pytest.raises(OcrError) as caught:
        recognize_image(image, executable=helper, timeout_seconds=2)
    assert caught.value.code == expected
    assert "x" * 100 not in str(caught.value)


def test_helper_timeout_is_bounded(tmp_path: Path) -> None:
    image = tmp_path / "synthetic.png"
    image.write_bytes(_png())
    helper = _helper(tmp_path / "ocr-helper", "import time; time.sleep(3)")
    with pytest.raises(OcrError) as caught:
        recognize_image(image, executable=helper, timeout_seconds=1)
    assert caught.value.code == "helper_timeout"


def test_malformed_line_is_rejected_not_silently_dropped(tmp_path: Path) -> None:
    image = tmp_path / "synthetic.png"
    image.write_bytes(_png())
    helper = _helper(
        tmp_path / "ocr-helper",
        "import json\nprint(json.dumps({'lines': [{'text': 'private body', 'box': [0.1], 'confidence': 0.7}]}))",
    )
    with pytest.raises(OcrError) as caught:
        recognize_image(image, executable=helper, timeout_seconds=2)
    assert caught.value.code == "invalid_helper_output"
    assert "private body" not in str(caught.value)


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None or os.getenv("CHAT_OCR_NATIVE_TEST") != "1",
    reason="macOS Vision native smoke requires an approved non-sandboxed run",
)
def test_native_helper_reads_synthetic_text_png(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "native" / "ocr" / "recognize.m"
    executable = tmp_path / "chat-image-ocr"
    build = subprocess.run(
        ["xcrun", "clang", "-fobjc-arc", str(source), "-framework", "Foundation", "-framework", "Vision", "-framework", "ImageIO", "-framework", "CoreGraphics", "-o", str(executable)],
        check=False, capture_output=True, text=True, timeout=90,
    )
    assert build.returncode == 0, build.stderr
    renderer_source = Path(__file__).resolve().parents[1] / "fixtures" / "render_synthetic_text.m"
    renderer = tmp_path / "render-synthetic-text"
    rendered = subprocess.run(
        ["xcrun", "clang", "-fobjc-arc", str(renderer_source), "-framework", "AppKit", "-o", str(renderer)],
        check=False, capture_output=True, text=True, timeout=90,
    )
    assert rendered.returncode == 0, rendered.stderr
    image = tmp_path / "synthetic.png"
    subprocess.run([str(renderer), str(image)], check=True, capture_output=True, timeout=10)
    assert any("OCR TEST 2026" in item.text for item in recognize_image(image, executable=executable, timeout_seconds=30))
