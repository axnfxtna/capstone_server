#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# tools/scp_to_pi5.sh
# Transfer essential PI 5 sender files to Raspberry Pi 5.
#
# Usage:
#   ./tools/scp_to_pi5.sh [pi5_user]
#
# Default user: pi
# Default host: 10.26.9.196
# ─────────────────────────────────────────────────────────────────────────────

set -e

PI5_USER="${1:-pi}"
PI5_HOST="10.26.9.196"
PI5_DIR="/home/${PI5_USER}/satu_sender"

SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "========================================"
echo "  Satu — SCP to PI 5"
echo "  Target : ${PI5_USER}@${PI5_HOST}:${PI5_DIR}"
echo "  Source : ${SERVER_DIR}"
echo "========================================"

# ── 1. Create destination directory on PI 5 ──────────────────────────────────
echo ""
echo "[1/4] Creating ${PI5_DIR} on PI 5..."
ssh "${PI5_USER}@${PI5_HOST}" "mkdir -p ${PI5_DIR}/docs ${PI5_DIR}/config"

# ── 2. Transfer design docs ───────────────────────────────────────────────────
echo ""
echo "[2/4] Transferring design docs..."
scp "${SERVER_DIR}/docs/pi5_sender_design.md"  "${PI5_USER}@${PI5_HOST}:${PI5_DIR}/docs/"
scp "${SERVER_DIR}/docs/pi5_design.md"         "${PI5_USER}@${PI5_HOST}:${PI5_DIR}/docs/"

# ── 3. Transfer config ────────────────────────────────────────────────────────
echo ""
echo "[3/4] Transferring config..."
scp "${SERVER_DIR}/config/pi5.yaml"            "${PI5_USER}@${PI5_HOST}:${PI5_DIR}/config/"

# ── 4. Print reminder ─────────────────────────────────────────────────────────
echo ""
echo "[4/4] Done. Files on PI 5:"
ssh "${PI5_USER}@${PI5_HOST}" "find ${PI5_DIR} -type f | sort"

echo ""
echo "========================================"
echo "  Next steps on PI 5:"
echo "    cd ${PI5_DIR}"
echo "    pip install requests sounddevice soundfile"
echo "    sudo apt-get install libsndfile1 portaudio19-dev"
echo ""
echo "  Read the design docs:"
echo "    docs/pi5_sender_design.md  ← what to SEND to the server"
echo "    docs/pi5_design.md         ← what to EXPOSE (audio_play, set_active)"
echo "    config/pi5.yaml            ← port / IP / audio settings"
echo "========================================"
