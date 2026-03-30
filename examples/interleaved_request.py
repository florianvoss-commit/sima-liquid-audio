#!/usr/bin/env python3

import argparse
from pathlib import Path

import requests

from audio_web_common import open_audio_file, print_stream, write_wav_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Interleaved request example for AudioWEB")
    parser.add_argument("--board-ip", default="127.0.0.1")
    parser.add_argument("--audio-path", type=Path, default=None)
    parser.add_argument("--text", default="")
    parser.add_argument(
        "--include-chat-history",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable or disable backend interleaved conversation history",
    )
    parser.add_argument("--output-wav", type=Path, default=Path("interleaved_response.wav"))
    args = parser.parse_args()

    if args.audio_path is None and not args.text.strip():
        raise SystemExit("Provide --audio-path and/or --text.")

    base_url = f"http://{args.board_ip}:9998"

    request_kwargs = {
        "stream": True,
        "timeout": 300,
    }

    if args.audio_path is not None:
        data = {
            "include_chat_history": "true" if args.include_chat_history else "false",
        }
        if args.text.strip():
            data["text"] = args.text.strip()
        request_kwargs["files"] = {"file": open_audio_file(args.audio_path)}
        request_kwargs["data"] = data
    else:
        request_kwargs["json"] = {
            "text": args.text.strip(),
            "include_chat_history": args.include_chat_history,
        }

    with requests.post(f"{base_url}/v1/realtime", **request_kwargs) as response:
        pcm_i16_bytes, sample_rate_hz, full_text, ttfa_sec = print_stream(response)

    if pcm_i16_bytes and sample_rate_hz:
        write_wav_file(args.output_wav, pcm_i16_bytes, sample_rate_hz)
        print(f"Saved wav: {args.output_wav}")
    if full_text:
        print("\nFull text:\n" + full_text)
    if ttfa_sec is not None:
        print(f"Time to first audio: {ttfa_sec:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
