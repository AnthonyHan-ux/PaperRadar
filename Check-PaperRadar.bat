@echo off
setlocal
set "PAPERRADAR_DIR=%LOCALAPPDATA%\Programs\PaperRadar-1.3.1"
set "PAPERRADAR_EXE=%PAPERRADAR_DIR%\PaperRadar.exe"
set "PAPERRADAR_CHECK=%TEMP%\PaperRadar-Startup-Check-%RANDOM%-%RANDOM%"
if not exist "%PAPERRADAR_EXE%" (
    echo FAIL: PaperRadar installation not found.
    if /I not "%~1"=="--no-pause" pause
    exit /b 1
)
mkdir "%PAPERRADAR_CHECK%" || exit /b 1
echo Checking the installed application with isolated test data...
start "" /wait /D "%PAPERRADAR_DIR%" "%PAPERRADAR_EXE%" --home "%PAPERRADAR_CHECK%\data" --smoke "%PAPERRADAR_CHECK%\window.png" 1>"%PAPERRADAR_CHECK%\stdout.log" 2>"%PAPERRADAR_CHECK%\stderr.log"
set "PAPERRADAR_EXIT=%ERRORLEVEL%"
if not "%PAPERRADAR_EXIT%"=="0" goto failed
if not exist "%PAPERRADAR_CHECK%\window.png" goto failed
echo PASS: Window rendered and the test database initialized.
echo Test files: "%PAPERRADAR_CHECK%"
if /I not "%~1"=="--no-pause" pause
exit /b 0

:failed
echo FAIL: Startup failed or was blocked. Exit code: %PAPERRADAR_EXIT%
echo Diagnostic files: "%PAPERRADAR_CHECK%"
echo For the verified local build, SmartScreen may require More info, then Run anyway.
if /I not "%~1"=="--no-pause" pause
exit /b 1
