@echo off
setlocal
set "PAPERRADAR_DIR=%LOCALAPPDATA%\Programs\PaperRadar-1.3.1"
set "PAPERRADAR_EXE=%PAPERRADAR_DIR%\PaperRadar.exe"
if not exist "%PAPERRADAR_EXE%" (
    echo PaperRadar is not installed at "%PAPERRADAR_EXE%".
    echo Restore the verified user installation before starting.
    pause
    exit /b 1
)
start "" /D "%PAPERRADAR_DIR%" "%PAPERRADAR_EXE%" %*
