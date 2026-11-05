@echo off
python -m pip install pillow requests pyinstaller
python -m PyInstaller --onefile --noconsole --name LiveScoreOverlay cl_overlay.py
echo.
echo Fertig! Die exe liegt in: dist\LiveScoreOverlay.exe
pause