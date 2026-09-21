@echo off
REM ============================================================
REM  行动序列合成图生成器 - 打包脚本
REM  双击本文件即可把 action_sequence_gui.py 打包成 exe。
REM  以后修改了 action_sequence_gui.py，重新双击本脚本即可。
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

REM 让 PyInstaller 的缓存放在项目目录下，保持打包过程自包含
set "PYINSTALLER_CONFIG_DIR=%~dp0.pyinstaller"

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name ActionSequenceComposer ^
  action_sequence_gui.py

echo.
if exist "dist\ActionSequenceComposer.exe" (
    echo [完成] 可执行文件: dist\ActionSequenceComposer.exe
) else (
    echo [失败] 未生成 exe，请查看上方报错。
)
echo.
echo 提示:
echo   - 如需给 exe 加图标: 在 --windowed 后面加一行  --icon 图标.ico
echo   - 如需打包成文件夹(启动更快,但不是一个单文件):
echo     把 --onefile 改成 --onedir 即可。
echo.
pause
