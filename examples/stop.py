#!/usr/bin/env python3

import argparse
import json

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop the active AudioWEB request")
    parser.add_argument("--board-ip", default="127.0.0.1")
    args = parser.parse_args()

    response = requests.post(f"http://{args.board_ip}:9998/stop", timeout=30)
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
