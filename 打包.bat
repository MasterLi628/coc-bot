@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 把源码打包成软件，输出到 dist\部落冲突挂机\ ……
python -c "import PyInstaller, cv2, numpy, pystray, PIL, customtkinter" >nul 2>nul || (
    echo 缺少打包用的库，先运行：  pip install -r requirements.txt
    pause
    exit /b 1
)
python build.py
echo.
pause
