#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SCRIPT_DIR}"

usage() {
  cat <<'EOF'
Usage: ./run.sh [options] [-- app.py options]

Modes:
  (default)           Start backend + frontend
  --frontend-only     Start only frontend app.py
  --backend-only      Start only sima_lmm backend

Options:
  -h, --help              Show this help

Environment overrides:
  LOG_FILE
EOF
}

MODE="both"
MODEL_PATH=""
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/console.log}"
APP_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --frontend-only)
      MODE="frontend"
      shift
      ;;
    --backend-only)
      MODE="backend"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      APP_ARGS+=("$@")
      break
      ;;
    *)
      APP_ARGS+=("$1")
      shift
      ;;
  esac
done

VENV_DIR="${SCRIPT_DIR}/.venv"
if [[ ! -x "${VENV_DIR}/bin/python3" ]]; then
  echo "❌ Python virtual env not found at ${VENV_DIR}."
  echo "💡 Run ./install.sh first."
  exit 1
fi

PYTHON_BIN="${VENV_DIR}/bin/python3"

detect_model_path() {
  local search_roots=("${ROOT_DIR}" "${ROOT_DIR}/Compiled_Models")
  local root
  local candidates=()
  local dir
  local i
  local selection

  for root in "${search_roots[@]}"; do
    [[ -d "${root}" ]] || continue
    while IFS= read -r dir; do
      if [[ -d "${dir}/devkit" && -d "${dir}/elf_files" ]]; then
        candidates+=("${dir}")
      fi
    done < <(find "${root}" -mindepth 1 -maxdepth 2 -type d | sort)
  done

  if [[ ${#candidates[@]} -eq 0 ]]; then
    return 1
  fi

  MODEL_PATH="${candidates[0]}"
  if [[ ${#candidates[@]} -gt 1 ]]; then
    echo "ℹ️ Multiple compiled models found:"
    for i in "${!candidates[@]}"; do
      printf "  %d) %s\n" "$((i + 1))" "${candidates[i]}"
    done

    if [[ ! -t 0 ]]; then
      echo "❌ Multiple models found but no interactive terminal is available."
      echo "💡 Keep only one compiled model folder under ${ROOT_DIR}."
      return 1
    fi

    while true; do
      read -r -p "Select model number [1-${#candidates[@]}]: " selection
      if [[ "${selection}" =~ ^[0-9]+$ ]] && (( selection >= 1 && selection <= ${#candidates[@]} )); then
        MODEL_PATH="${candidates[selection-1]}"
        echo "ℹ️ Selected model path: ${MODEL_PATH}"
        break
      fi
      echo "Invalid selection. Enter a number between 1 and ${#candidates[@]}."
    done
  else
    echo "ℹ️ Auto-detected model path: ${MODEL_PATH}"
  fi
}

if [[ "${MODE}" != "frontend" ]]; then
  if ! detect_model_path; then
    echo "❌ Could not auto-detect a compiled model."
    echo "💡 Expected a folder containing both devkit/ and elf_files/ under ${ROOT_DIR}."
    exit 1
  fi

  if [[ ! -d "${MODEL_PATH}" || ! -d "${MODEL_PATH}/devkit" || ! -d "${MODEL_PATH}/elf_files" ]]; then
    echo "❌ Invalid model path: ${MODEL_PATH}"
    echo "💡 Expected subfolders: devkit/ and elf_files/"
    exit 1
  fi

  if ! "${PYTHON_BIN}" -c "import sima_lmm" >/dev/null 2>&1; then
    echo "❌ sima_lmm module unavailable in ${VENV_DIR}."
    echo "💡 Run ./install.sh first so the app venv gets the sima_lmm wheel."
    exit 1
  fi
fi

start_backend() {
  sudo -E "${PYTHON_BIN}" -m sima_lmm.devkit.devkit_demo run \
    "${MODEL_PATH}" \
    --mode web
}

ensure_sudo_ready() {
  echo "🔐 Requesting sudo access for backend launch..."
  sudo -v
}

start_frontend() {
  "${PYTHON_BIN}" app.py "${APP_ARGS[@]}"
}

if [[ "${MODE}" == "backend" ]]; then
  ensure_sudo_ready
  start_backend 2>&1 | tee "${LOG_FILE}"
  exit ${PIPESTATUS[0]}
fi

if [[ "${MODE}" == "frontend" ]]; then
  exec "${PYTHON_BIN}" app.py "${APP_ARGS[@]}"
fi

echo "🚀 Starting backend (log: ${LOG_FILE})"
ensure_sudo_ready
start_backend 2>&1 | tee "${LOG_FILE}" &
BACKEND_PID=$!

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

sleep 2
echo "🚀 Starting frontend against local backend at 127.0.0.1:9998"
exec "${PYTHON_BIN}" app.py "${APP_ARGS[@]}"
