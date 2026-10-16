@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=
where python >nul 2>nul && python -c "import cv2, numpy" >nul 2>nul && set PY=python
if not defined PY (
    echo 找不到能用的 Python。请先安装 Python 3（安装时勾选 Add python.exe to PATH），
    echo 然后在命令行运行：  pip install opencv-python numpy
    pause
    exit /b 1
)

start "" %PY%w 控制面板.pyw
