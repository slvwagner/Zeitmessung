@echo off
setlocal

if "%~1"=="" exit /b 1

for %%F in (%*) do (
    type "%%~fF"
)

exit /b 0
