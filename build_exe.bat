@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [1/3] Checking build dependencies...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

echo [2/3] Building Morgana Companion executable...
python -m PyInstaller --noconfirm --clean MorganaCompanion.spec
if errorlevel 1 exit /b 1

echo [3/3] Creating release archive dist\Morgana-Companion-win64.zip...
if exist "dist\Morgana-Companion-win64.zip" del "dist\Morgana-Companion-win64.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Morgana Companion' -DestinationPath 'dist\Morgana-Companion-win64.zip' -Force"
if errorlevel 1 exit /b 1

echo.
echo Done!
echo   Executable : dist\Morgana Companion\Morgana Companion.exe
echo   Release zip: dist\Morgana-Companion-win64.zip
endlocal
