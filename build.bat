@echo off
python -m pip install pillow requests pystray pyinstaller
python -m PyInstaller --onefile --noconsole --name LiveScoreOverlay --hidden-import pystray._win32 cl_overlay.py
echo.
echo Fertig! Die exe liegt in: dist\LiveScoreOverlay.exe
pause
