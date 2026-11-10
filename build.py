"""把源码打包成"部落冲突挂机"软件（双击"打包.bat"）。

输出到 dist\\部落冲突挂机\\：部落冲突挂机.exe + _internal\\ + templates\\ + assets\\ + 使用说明.md
重新打包只覆盖程序和模板，不动里面的 config.json（设置）和 bot.log（日志）。
"""
import shutil
import tempfile
import os
from pathlib import Path

import PyInstaller.__main__

from cocbot.config import APP_NAME

HERE = Path(__file__).parent
NAME = APP_NAME
OUT = Path(os.environ.get("COCBOT_BUILD_OUT", HERE / "dist" / NAME))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cocbuild_"))
    icon = HERE / "assets" / "icon.ico"
    PyInstaller.__main__.run([
        str(HERE / "app.py"), "--name", NAME, "--windowed", "--noconfirm", "--clean",
        "--icon", str(icon), "--hidden-import", "pystray._win32", "--collect-data", "customtkinter",
        "--distpath", str(tmp / "dist"), "--workpath", str(tmp / "work"), "--specpath", str(tmp),
    ])
    built = tmp / "dist" / NAME
    OUT.mkdir(parents=True, exist_ok=True)
    for old in ("_internal", "templates", "assets"):
        shutil.rmtree(OUT / old, ignore_errors=True)
    shutil.copytree(built / "_internal", OUT / "_internal")
    for f in (OUT / "_internal" / "cv2").glob("opencv_videoio_ffmpeg*.dll"):
        f.unlink()  # 播放视频用的，30MB，用不上
    shutil.copy2(built / f"{NAME}.exe", OUT / f"{NAME}.exe")
    shutil.copytree(HERE / "templates", OUT / "templates")
    shutil.copytree(HERE / "assets", OUT / "assets", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    shutil.copy2(HERE / "docs" / "使用说明.md", OUT / "使用说明.md")
    if (HERE / "config.json").exists() and not (OUT / "config.json").exists():
        shutil.copy2(HERE / "config.json", OUT / "config.json")  # 第一次打包时带上现在的设置
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n打包完成：{OUT}")


if __name__ == "__main__":
    main()
