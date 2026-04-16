@echo off
setlocal

if "%~1"=="" exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$path = '%~1'; $dir = Split-Path -Parent $path; if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }; if (Test-Path $path) { (Get-Item $path).LastWriteTime = Get-Date } else { New-Item -ItemType File -Path $path | Out-Null }"

exit /b %ERRORLEVEL%
