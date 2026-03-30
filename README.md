# SiMa Liquid Audio Demo

This app is the browser/UI layer for the liquid-audio demo. It sits between the browser and the backend model server:

- browser <-> `app.py` via Flask + Socket.IO
- `app.py` <-> `sima_lmm` backend via HTTP/SSE

For audio modes, the backend is expected to expose the new `AudioWEB` endpoints:

- `POST /v1/audio/transcriptions`
- `POST /v1/audio/speech`
- `POST /v1/realtime`
- `POST /stop`
- `POST /reset_conversation`

`app.py` consumes those SSE streams server-side and forwards the streamed text/audio updates to the browser over Socket.IO.

## Install

Recommended setup layout:

```text
liquid_audio/
  sima-liquid-audio/
  sima_lmm-<version>.whl
  <compiled-model-folder>/
```

### 1. Create a workspace folder

```bash
mkdir -p liquid_audio
cd liquid_audio
```

### 2. Clone this GitHub repo into it

```bash
git clone git@github.com:florianvoss-commit/sima-liquid-audio.git
```

### 3. Download the `sima_lmm` wheel into the parent `liquid_audio` folder

Download the wheel from:

- <https://drive.google.com/file/d/1JiEkKB3Nq18BP_VxzUdiOvXBBCMEfQYc/view?usp=sharing>

and place it here:

```text
liquid_audio/sima_lmm-2.1.0-cp311-cp311-linux_aarch64.whl
```

### 4. Clone the Hugging Face compiled model repo into the same `liquid_audio` folder

Clone:

- `florianvoss/LFM2.5-Audio-1.5B_compiled`

The compiled model should sit next to `sima-liquid-audio`, not inside it.

Example:

```bash
cd ..
git clone https://huggingface.co/florianvoss/LFM2.5-Audio-1.5B_compiled
```

Example result:

```text
liquid_audio/
  sima-liquid-audio/
  sima_lmm-2.1.0-cp311-cp311-linux_aarch64.whl
  LFM2.5-Audio-1.5B_compiled/
```

### 5. Run the app installer

```bash
cd sima-liquid-audio
./install.sh
```

What this does:

- creates a local virtual environment in [`.venv`](./.venv)
- installs Python dependencies from [`requirements.txt`](./requirements.txt)
- installs the latest parent-directory `sima_lmm-*.whl`

Important:

- `install.sh` expects a built `sima_lmm` wheel to exist in the parent directory
- the venv is created with `--system-site-packages`
- the script force-reinstalls the wheel into the app venv so it is used even if another `sima_lmm` version exists globally

## Run

Show help:

```bash
./run.sh --help
```

Current usage:

```text
Usage: ./run.sh [options] [-- app.py options]

Modes:
  (default)           Start backend + frontend
  --frontend-only     Start only frontend app.py
  --backend-only      Start only sima_lmm backend

Options:
  -h, --help              Show this help
```

Behavior:

- `run.sh` auto-detects a compiled model in the parent directory
- backend startup uses:

```bash
python -m sima_lmm.devkit.devkit_demo run <MODEL_PATH> --mode web
```

- combined mode prompts for `sudo` first, then starts backend and frontend
- `app.py` always talks to the local backend at `127.0.0.1:9998`
- `--frontend-only` does not require a local model checkout and is useful if the backend is already running on the same device

## Typical Flows

Start backend + frontend:

```bash
./run.sh
```

Start only frontend against an existing backend:

```bash
./run.sh --frontend-only
```

Start only backend:

```bash
./run.sh --backend-only
```

## UI Modes

The current UI supports:

- `ASR`
- `TTS`
- `Interleaved (Speech2Speech)`

Notes:

- ASR streams transcript updates into the chat UI
- TTS streams audio chunks from the backend and plays them in the browser
- interleaved sends microphone audio to `/v1/realtime` and streams both text and audio back
- the editable prompt in the UI is currently sent to interleaved mode as ordinary `text` input, not as a privileged backend system prompt

## Conversation State

For interleaved mode:

- when `Include chat history` is enabled, `app.py` sends `include_chat_history=true`
- the backend preserves interleaved conversation state across turns
- `/clear-history` resets both frontend history and backend interleaved state
- `/stop` stops the active request; for interleaved runs this also resets backend continuation state

## Example Request Scripts

This repo includes small example scripts for direct backend interaction:

- [`examples/interleaved_request.py`](./examples/interleaved_request.py)
- [`examples/asr_request.py`](./examples/asr_request.py)
- [`examples/tts_request.py`](./examples/tts_request.py)
- [`examples/reset_history.py`](./examples/reset_history.py)
- [`examples/stop.py`](./examples/stop.py)

Supported TTS voices:

- `uk_female`
- `uk_male`
- `us_female`
- `us_male`

Examples:

```bash
python examples/asr_request.py --audio-path /path/to/audio.wav
python examples/tts_request.py --input "Hello there" --voice uk_female
```

```bash
python examples/interleaved_request.py --audio-path /path/to/audio.wav
python examples/interleaved_request.py --audio-path /path/to/audio.wav --text "Keep it short." --include-chat-history
python examples/interleaved_request.py --audio-path /path/to/audio.wav --no-include-chat-history
python examples/reset_history.py
python examples/stop.py
```

Optional:

```bash
python examples/asr_request.py --board-ip 192.168.1.10 --audio-path /path/to/audio.wav
python examples/tts_request.py --board-ip 192.168.1.10 --input "Hello there" --voice us_male
```

```bash
python examples/interleaved_request.py --board-ip 192.168.1.10 --audio-path /path/to/audio.wav
python examples/reset_history.py --board-ip 192.168.1.10
python examples/stop.py --board-ip 192.168.1.10
```
