#!/bin/bash
set -e

cd "$(dirname "$0")"

# Check for --tts-only flag
TTS_ONLY=false
if [[ "$1" == "--tts-only" ]]; then
    TTS_ONLY=true
    echo "🔁 Running in TTS-only mode. Will download ONNX files and exit."
fi

echo "🔎 Scanning for *.onnx.json files in assets/..."


# 🔍 Check if running on a Modalix platform
model_path="/proc/device-tree/model"
if [[ -f "$model_path" ]]; then
    model=$(tr -d '\0' < "$model_path" | head -n 1)
    if [[ "$model" == *"Modalix"* ]]; then
        echo "🟢 Detected Modalix platform (model=$model)"
    else
        echo "❌ This script must be run on a Modalix device."
        echo "💡 model reported: '$model'"
        exit 1
    fi
else
    echo "👉 The remainder of the installation script must be run directly on a Modalix board."
    echo "💡 As you are running on a host, use NFS to mount the current folder on the Modalix target"
    echo "   and run [install.sh] in the [simaai-genai-demo] folder."
    exit 1
fi

if command -v apt >/dev/null 2>&1; then
    echo "✅ apt detected. Installing system tools required for LLiMa..."
    sudo apt update -y && sudo apt install -y git gawk ffmpeg rsync
else
    echo "❌ apt not found. This system may not be Debian/Ubuntu-based."
    echo "Please install git manually using your system's package manager."
fi

VENV_DIR=".venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "📜 Creating virtual environment at ${VENV_DIR}..."
    python3 -m venv --system-site-packages "$VENV_DIR"
fi

echo "📜 Activating virtual environment (${VENV_DIR})..."
source "${VENV_DIR}/bin/activate"

python3 -m pip install --upgrade pip
pip3 install setuptools wheel

echo "📦 Installing Python dependencies from requirements.txt ..."
pip_install_cmd=(python3 -m pip install -r requirements.txt)

# Find latest matching sima_lmm wheel in parent directory
SIMA_LMM_WHEEL=$(ls ../sima_lmm-*.whl 2>/dev/null | sort -V | tail -n 1)
if [[ -n "$SIMA_LMM_WHEEL" && -f "$SIMA_LMM_WHEEL" ]]; then
    echo "📦 Installing latest sima_lmm wheel into the venv: $SIMA_LMM_WHEEL"
else
    echo "❌ No sima_lmm wheel found in parent directory."
    exit 1
fi

# 🧩 Run pip installation with logging + live dot progress
LOG_FILE="install.log"
echo "🧰 Starting dependency installation process, this will take a while... logging to $LOG_FILE"
: > "$LOG_FILE"  # clear any previous log

# Run pip install command in background, log all output
{
    echo "===== $(date) ====="
    echo "Running pip install command:"
    printf '%q ' "${pip_install_cmd[@]}"
    export CMAKE_ARGS="-DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF -DCMAKE_BUILD_PARALLEL_LEVEL=$(nproc)"
    export CMAKE_BUILD_PARALLEL_LEVEL=$(nproc)
    echo
    "${pip_install_cmd[@]}"
    python3 -m pip install --force-reinstall --no-deps "$SIMA_LMM_WHEEL"
    ret=$?
    echo "Exit code: $ret"
    exit $ret
} >"$LOG_FILE" 2>&1 &
INSTALL_PID=$!

# Function to show progress dots as log file grows
show_dots() {
    local pid=$1
    local last_size=0
    while ps -p $pid >/dev/null 2>&1; do
        if [[ -f "$LOG_FILE" ]]; then
            local new_size
            new_size=$(wc -l < "$LOG_FILE")
            if (( new_size > last_size )); then
                printf "."
                last_size=$new_size
            fi
        fi
        sleep 1
    done
    printf "\n"
}

# Start dot progress monitor
show_dots $INSTALL_PID
wait $INSTALL_PID
INSTALL_RESULT=$?

if [[ $INSTALL_RESULT -eq 0 ]]; then
    echo "✅ Python dependencies installed successfully!"
else
    echo -e "\n❌ Installation failed. Check $LOG_FILE for details."
    tail -n 20 "$LOG_FILE"
    exit 1
fi

# WHEEL_FILE=$(ls ../sima_utils-*.whl 2>/dev/null | sort -V | tail -n 1)
# if [[ -n "$WHEEL_FILE" && -f "$WHEEL_FILE" ]]; then
#     echo "📦 Installing latest sima_utils wheel: $WHEEL_FILE"
#     pip install "$WHEEL_FILE" --no-deps
# else
#     echo "⚠️  No sima_utils wheel file found. Skipping inference server installation."
# fi

echo "✅ Installation complete! Virtual environment is ready."
