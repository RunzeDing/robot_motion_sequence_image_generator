#!/usr/bin/env bash
# ============================================================
#  行动序列合成图生成器 - macOS 打包脚本
#  用法: 在终端里运行  bash build_mac.sh
#  前置: 已安装 Python3 + PyInstaller + tkinter
#        (brew install python-tk; pip3 install numpy opencv-python pillow pyinstaller)
# ============================================================
set -e
cd "$(dirname "$0")"
export PYINSTALLER_CONFIG_DIR="$(pwd)/.pyinstaller"

# macOS 上用 --windowed（不带 --onefile）会生成 .app 应用包，双击即可运行
python3 -m PyInstaller --noconfirm --clean --windowed \
  --name ActionSequenceComposer action_sequence_gui.py

echo ""
echo "[完成] 应用位于: dist/ActionSequenceComposer.app"
echo "       把 .app 拖进 /Applications 即可，双击运行。"
