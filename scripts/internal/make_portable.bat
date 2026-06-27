@echo off
REM Thin wrapper -> Python build script (avoids cp949/.bat encoding issues).
REM Recommended: run "python scripts\build.py portable" directly.
cd /d "%~dp0..\.."
python scripts\build.py portable
pause
