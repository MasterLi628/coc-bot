@echo off
chcp 65001 >nul
title 部落冲突挂机（关掉这个窗口就停止）
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=
where python >nul 2>nul && python -c "import cv2, numpy" >nul 2>nul && set PY=python
if not defined PY (
    echo 找不到能用的 Python。请先安装 Python 3（安装时勾选 Add python.exe to PATH），
    echo 然后在命令行运行：  pip install opencv-python numpy
    pause
    exit /b 1
)

echo 先确认：雷电模拟器已经打开、游戏停在村庄主界面。
echo 运行期间不要手动操作模拟器；想停就直接关掉这个窗口。
echo 要改模拟器路径、进攻开关等，请先关掉本窗口，双击"挂机控制面板.bat"。
echo.

%PY% bot.py

echo.
echo 脚本已退出。
pause
