#!/usr/bin/env bash
# ============================================================
#  行动序列合成图生成器 - Linux 打包脚本
#  用法: 在终端里运行  bash build_linux.sh
#  前置: 已安装 Python3 + tkinter + PyInstaller
#        (Debian/Ubuntu: sudo apt install python3-tk)
#        (pip3 install numpy opencv-python pillow pyinstaller)
# ============================================================
set -e
cd "$(dirname "$0")"
export PYINSTALLER_CONFIG_DIR="$(pwd)/.pyinstaller"

python3 -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name ActionSequenceComposer action_sequence_gui.py

echo ""
echo "[完成] 可执行文件位于: dist/ActionSequenceComposer"
echo "       在终端里运行: chmod +x dist/ActionSequenceComposer  后双击/运行即可。"
