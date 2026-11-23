# -*- coding: utf-8 -*-
"""
一键打包为 exe（Windows）：
  python build_exe.py

流程：生成图标 → PyInstaller 单文件打包（窗口程序，内嵌 app.ico）
输出：dist/YCrCb工具.exe
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "YCrCb工具"
ENTRY = "ycrcb_tool.py"
ICON = "app.ico"


def run(cmd):
    print(">", " ".join(cmd))
    subprocess.check_call(cmd, cwd=HERE)


def main():
    # 1. 图标（不存在或源码更新后重建）
    if not os.path.isfile(os.path.join(HERE, ICON)):
        run([sys.executable, "make_icon.py"])

    # 2. 清理旧产物
    for d in ("build", "dist", f"{NAME}.spec"):
        p = os.path.join(HERE, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            os.remove(p)

    # 3. PyInstaller 打包（ycrcb_core / widgets 由入口自动分析打包）
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed",
        "--name", NAME,
        "--icon", ICON,
        "--version-file", "version_info.txt",
        "--add-data", f"{ICON};.",
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        ENTRY,
    ]
    run(cmd)

    exe = os.path.join(HERE, "dist", NAME + ".exe")
    print()
    if os.path.isfile(exe):
        size = os.path.getsize(exe) / 1024 / 1024
        print(f"打包完成：{exe}  ({size:.1f} MB)")
    else:
        print("打包失败：未找到输出 exe")
        sys.exit(1)


if __name__ == "__main__":
    main()
