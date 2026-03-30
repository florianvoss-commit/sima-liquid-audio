#!/usr/bin/env python3

import argparse
from pathlib import Path

import requests

from audio_web_common import print_stream, write_wav_file


def main() -> int:
    parser = argparse.ArgumentParser(description="TTS request example for AudioWEB")
    parser.add_argument("--board-ip", default="127.0.0.1")
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--voice",
        default="uk_female",
        choices=["uk_female", "uk_male", "us_female", "us_male"],
    )
    parser.add_argument("--output-wav", type=Path, default=Path("tts_response.wav"))
    args = parser.parse_args()

    with requests.post(
        f"http://{args.board_ip}:9998/v1/audio/speech",
        json={"input": args.input, "voice": args.voice},
        stream=True,
        timeout=300,
    ) as response:
        pcm_i16_bytes, sample_rate_hz, _, ttfa_sec = print_stream(response)

    if pcm_i16_bytes and sample_rate_hz:
        write_wav_file(args.output_wav, pcm_i16_bytes, sample_rate_hz)
        print(f"Saved wav: {args.output_wav}")
    if ttfa_sec is not None:
        print(f"Time to first audio: {ttfa_sec:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
