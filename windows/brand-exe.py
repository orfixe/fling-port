# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Give the Fling launcher its own name and icon, so Task Manager shows "Fling", not "Python".

    uv run --no-project --with pyinstaller python brand-exe.py <Fling.exe> <fling.ico> <version>

<Fling.exe> is a copy of the venv's base pythonw.exe (install.ps1 makes it). Only its
resources change: the icon and the version info (FileDescription is what Task Manager
lists). PyInstaller is used just for its resource helpers, it is not installed into Fling.
"""
import sys

from PyInstaller.utils.win32.icon import CopyIcons
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                 StringTable, VarFileInfo, VarStruct,
                                                 VSVersionInfo, write_version_info_to_executable)


def main(exe: str, ico: str, version: str) -> None:
    import tempfile
    from PyInstaller import config
    config.CONF.setdefault("workpath", tempfile.mkdtemp(prefix="fling-brand-"))   # CopyIcons wants one
    nums = tuple(int(n) for n in (version.split(".") + ["0"] * 4)[:4])
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0,
                          OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("FileDescription", "Fling"),
                StringStruct("ProductName", "Fling"),
                StringStruct("InternalName", "Fling"),
                StringStruct("OriginalFilename", "Fling.exe"),
                StringStruct("FileVersion", version),
                StringStruct("ProductVersion", version),
                StringStruct("Comments", "Cast to Chromecast / Google TV and control the TV"),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ])
    CopyIcons(exe, ico)
    write_version_info_to_executable(exe, info)
    print(f"branded {exe} as Fling {version}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
