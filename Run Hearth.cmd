@echo off
set "HEARTH_APP=%~dp0dist\Project Hearth TV.exe"
if not exist "%HEARTH_APP%" (
  echo Project Hearth TV.exe was not found in the dist folder.
  pause
  exit /b 1
)
start "" "%HEARTH_APP%"
