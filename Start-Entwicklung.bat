@echo off
rem Startet KleinFaktura aus dem Quellcode (Entwicklung) mit getrennten Testdaten unter
rem %LOCALAPPDATA%\KleinFaktura-Entwicklung. Fuer die echte Arbeit die installierte Release-Exe nutzen.
cd /d "%~dp0"
start "" pythonw main.py
