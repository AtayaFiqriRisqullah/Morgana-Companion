#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/3] Checking build dependencies..."
python3 -m pip install -r requirements.txt pyinstaller

echo "[2/3] Building Morgana Companion binary for Linux..."
python3 -m PyInstaller --noconfirm --clean MorganaCompanion.spec

echo "[3/3] Creating release archive dist/Morgana-Companion-linux-x64.tar.gz..."
rm -f "dist/Morgana-Companion-linux-x64.tar.gz"
tar -czf "dist/Morgana-Companion-linux-x64.tar.gz" -C dist "Morgana Companion"

echo
echo "Done!"
echo "  Executable : dist/Morgana Companion/Morgana Companion"
echo "  Release tar: dist/Morgana-Companion-linux-x64.tar.gz"
