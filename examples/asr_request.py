#!/usr/bin/env python3

import argparse
from pathlib import Path

import requests

from audio_web_common import open_audio_file, print_stream


def main() -> int:
    parser = argparse.ArgumentParser(description="ASR request example for AudioWEB")
    parser.add_argument("--board-ip", default="127.0.0.1")
    parser.add_argument("--audio-path", type=Path, required=True)
    args = parser.parse_args()

    with requests.post(
        f"http://{args.board_ip}:9998/v1/audio/transcriptions",
        files={"file": open_audio_file(args.audio_path)},
        stream=True,
        timeout=300,
    ) as response:
        _, _, full_text, _ = print_stream(response)

    if full_text:
        print("\nFull text:\n" + full_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
