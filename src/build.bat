@echo off
chcp 65001 > nul
title TokenChecker - Build EXE

echo.
echo ================================================
echo   TokenChecker - EXE Build
echo ================================================
echo.

rem --- Check Python ---
python --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please run setup.bat first.
    pause & exit /b 1
)

rem --- Install PyInstaller ---
echo [1/4] Installing PyInstaller...
pip install pyinstaller --quiet
if errorlevel 1 ( echo [FAIL] PyInstaller install failed & pause & exit /b 1 )
echo       OK

rem --- Generate icon ---
echo [2/4] Generating icon...
python -c "
from PIL import Image, ImageDraw
sz = 256
img = Image.new('RGBA', (sz, sz), (0,0,0,0))
d = ImageDraw.Draw(img)
d.ellipse([2,2,sz-2,sz-2], fill=(30,41,59,255))
half = sz//2; r = half//2 - 10; thick = max(10, sz//13)
# Claude (left, purple)
d.arc([half//2-r, half-r, half//2+r, half+r], 0, 360, fill=(100,50,200,80), width=thick)
d.arc([half//2-r, half-r, half//2+r, half+r], -90, 200, fill=(167,139,250,255), width=thick)
# Codex (right, green)
d.arc([half+half//2-r, half-r, half+half//2+r, half+r], 0, 360, fill=(10,100,60,80), width=thick)
d.arc([half+half//2-r, half-r, half+half//2+r, half+r], -90, 130, fill=(52,211,153,255), width=thick)
img.save('_tc_icon.ico', format='ICO', sizes=[(256,256),(64,64),(32,32),(16,16)])
print('OK')
" 2> nul
if errorlevel 1 (
    echo       [WARN] Icon generation failed, building without icon
    set ICON_ARG=
) else (
    echo       OK
    set ICON_ARG=--icon _tc_icon.ico
)

rem --- Build EXE ---
echo [3/4] Building EXE (may take 1-2 minutes)...
pyinstaller --onefile --windowed --name TokenChecker %ICON_ARG% ^
    --collect-all pystray ^
    --hidden-import PIL._tkinter_finder ^
    token_checker.py
if errorlevel 1 ( echo [FAIL] Build failed & pause & exit /b 1 )

rem --- Copy output ---
echo [4/4] Copying EXE...
if exist dist\TokenChecker.exe (
    copy /Y dist\TokenChecker.exe .\TokenChecker.exe > nul
    echo       OK
) else (
    echo [FAIL] EXE not found in dist\
    pause & exit /b 1
)

rem --- Cleanup ---
if exist build         rmdir /s /q build
if exist dist          rmdir /s /q dist
if exist __pycache__   rmdir /s /q __pycache__
if exist _tc_icon.ico  del _tc_icon.ico
if exist TokenChecker.spec del TokenChecker.spec

echo.
echo ================================================
echo   Build complete!
echo.
echo   TokenChecker.exe is ready.
echo   Double-click it to launch.
echo ================================================
echo.
pause
