#!/usr/bin/env python3

import base64
import json
from pathlib import Path
import time
import wave

import requests


def iter_sse(response: requests.Response):
    event_name = None
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line or ""
        if not line:
            if event_name is not None and data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            data_lines.append(line.partition(":")[2].lstrip())


def print_stream(response: requests.Response):
    response.raise_for_status()
    audio_bytes = bytearray()
    audio_sample_rate = None
    text_parts: list[str] = []
    stream_start = time.monotonic()
    time_to_first_audio_sec = None

    for event_name, data in iter_sse(response):
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = data

        if isinstance(payload, dict) and payload.get("type") == "text_chunk":
            text = payload.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)

        if isinstance(payload, dict) and payload.get("type") == "audio_chunk":
            audio_b64 = payload.pop("audio_pcm_i16_b64", "")
            if audio_b64:
                if time_to_first_audio_sec is None:
                    time_to_first_audio_sec = time.monotonic() - stream_start
                audio_bytes.extend(base64.b64decode(audio_b64))
                audio_sample_rate = payload.get("sample_rate_hz", audio_sample_rate)
                payload["audio_pcm_i16_b64_len"] = len(audio_b64)

        print(f"[{event_name}] {json.dumps(payload, indent=2) if isinstance(payload, dict) else payload}")

    return bytes(audio_bytes), audio_sample_rate, "".join(text_parts), time_to_first_audio_sec


def write_wav_file(path: Path, pcm_i16_bytes: bytes, sample_rate_hz: int):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm_i16_bytes)


def open_audio_file(path: Path):
    return (path.name or "audio.wav", path.read_bytes(), "audio/wav")
