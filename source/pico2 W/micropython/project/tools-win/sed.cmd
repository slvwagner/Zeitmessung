@echo off
setlocal

if "%~1"=="" exit /b 1

set "SED_EXPR=%~1"

if /I "%SED_EXPR%"=="s/^Q(.*)/\"&\"/" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$input | ForEach-Object { $_ -replace '^Q(.*)$', '\"$0\"' }"
    exit /b %ERRORLEVEL%
)

if /I "%SED_EXPR%"=="s/^\\\"\\(Q(.*)\\)\\\"/\\1/" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$input | ForEach-Object { $_ -replace '^\"(Q\(.*\))\"$', '$1' }"
    exit /b %ERRORLEVEL%
)

echo Unsupported sed expression: %SED_EXPR% 1>&2
exit /b 1
