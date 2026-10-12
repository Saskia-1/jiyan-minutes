"""Local macOS screenshot OCR boundary."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import selectors
import stat
import struct
import subprocess
import time


_MAX_IMAGE_BYTES = 15 * 1024 * 1024
_MAX_PIXELS = 40_000_000
_MAX_HELPER_BYTES = 1024 * 1024
_MAX_LINES = 10_000
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    box: tuple[float, float, float, float]
    confidence: float


class OcrError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def recognize_image(path: Path, *, executable: Path, timeout_seconds: int) -> tuple[OcrLine, ...]:
    image = Path(path)
    try:
        metadata = image.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_IMAGE_BYTES:
            raise OcrError("image_limit_exceeded")
        data = image.read_bytes()
    except OcrError:
        raise
    except OSError:
        raise OcrError("unsupported_image") from None
    width, height = _image_dimensions(data)
    if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
        raise OcrError("image_limit_exceeded")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        raise ValueError("OCR timeout must be 1 to 60 seconds")
    helper = Path(executable)
    try:
        helper_metadata = helper.lstat()
        if not stat.S_ISREG(helper_metadata.st_mode) or not os.access(helper, os.X_OK):
            raise OcrError("helper_unavailable")
    except OcrError:
        raise
    except OSError:
        raise OcrError("helper_unavailable") from None
    output = _run_helper(helper, image, timeout_seconds)
    return _parse_lines(output)


def _image_dimensions(data: bytes) -> tuple[int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 33 or data[12:16] != b"IHDR" or struct.unpack(">I", data[8:12])[0] != 13:
            raise OcrError("unsupported_image")
        return struct.unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        raise OcrError("unsupported_image")
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            break
        marker = data[position + 1]
        position += 2
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        segment_length = struct.unpack(">H", data[position:position + 2])[0]
        if segment_length < 2 or position + segment_length > len(data):
            break
        if marker in _JPEG_SOF:
            if segment_length < 7:
                break
            height, width = struct.unpack(">HH", data[position + 3:position + 7])
            return width, height
        position += segment_length
    raise OcrError("unsupported_image")


def _run_helper(executable: Path, image: Path, timeout_seconds: int) -> bytes:
    try:
        process = subprocess.Popen(
            [str(executable), str(image)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, close_fds=True,
        )
    except OSError:
        raise OcrError("helper_unavailable") from None
    assert process.stdout is not None and process.stderr is not None
    streams = selectors.DefaultSelector()
    streams.register(process.stdout, selectors.EVENT_READ)
    streams.register(process.stderr, selectors.EVENT_READ)
    output = bytearray()
    stderr_size = 0
    deadline = time.monotonic() + timeout_seconds
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OcrError("helper_timeout")
            for key, _ in streams.select(remaining):
                part = os.read(key.fileobj.fileno(), 65536)
                if not part:
                    streams.unregister(key.fileobj)
                    continue
                if key.fileobj is process.stdout:
                    output.extend(part)
                    if len(output) > _MAX_HELPER_BYTES:
                        raise OcrError("helper_output_limit")
                else:
                    stderr_size += len(part)
                    if stderr_size > 65536:
                        raise OcrError("helper_output_limit")
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise OcrError("helper_failed")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise OcrError("helper_timeout") from None
    finally:
        streams.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _parse_lines(output: bytes) -> tuple[OcrLine, ...]:
    try:
        document = json.loads(output.decode("utf-8"))
        if not isinstance(document, dict) or set(document) != {"lines"}:
            raise ValueError
        raw_lines = document["lines"]
        if not isinstance(raw_lines, list) or len(raw_lines) > _MAX_LINES:
            raise ValueError
        lines: list[OcrLine] = []
        for item in raw_lines:
            if not isinstance(item, dict) or set(item) != {"text", "box", "confidence"}:
                raise ValueError
            text = item["text"]
            box = item["box"]
            confidence = item["confidence"]
            if not isinstance(text, str) or len(text) > 20_000 or not isinstance(box, list) or len(box) != 4:
                raise ValueError
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in box):
                raise ValueError
            if box[0] + box[2] > 1.001 or box[1] + box[3] > 1.001:
                raise ValueError
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError
            lines.append(OcrLine(text=text, box=tuple(float(value) for value in box), confidence=float(confidence)))
        return tuple(lines)
    except (UnicodeError, ValueError, TypeError, KeyError):
        raise OcrError("invalid_helper_output") from None
