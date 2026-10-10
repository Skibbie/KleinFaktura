@echo off
rem Erstellt eine eigenständige KleinFaktura.exe (Ordner dist\KleinFaktura) und, falls Inno Setup 6 installiert
rem ist, den Installer dist\KleinFaktura-Setup-X.Y.Z.exe
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
python tools\make_splash.py || goto :eof
python -m PyInstaller --noconfirm --windowed --name KleinFaktura --icon assets\icon.ico --splash build\splash.png ^
  --add-data "assets;assets" --add-data "app\db\schema.sql;app\db" --add-data "docs;docs" ^
  --collect-data facturx --collect-data iso4217 --collect-data stdnum main.py || goto :eof
copy /y CHANGELOG.md dist\KleinFaktura\ >nul
copy /y LICENSE dist\KleinFaktura\ >nul
copy /y README.md dist\KleinFaktura\ >nul
echo.
echo Fertig: dist\KleinFaktura\KleinFaktura.exe
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  for /f %%v in ('python -c "from app import config; print(config.APP_VERSION)"') do set "VER=%%v"
  call "%%ISCC%%" /Qp "/DAppVersion=%%VER%%" installer\KleinFaktura.iss && call echo Installer: dist\KleinFaktura-Setup-%%VER%%.exe
) else (
  echo Inno Setup 6 nicht gefunden - Installer wird nicht gebaut.
)
pause
