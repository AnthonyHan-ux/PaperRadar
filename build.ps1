<#
.SYNOPSIS
    One-command build for PaperRadar (Windows, Python 3.11+).

.DESCRIPTION
    1. creates .venv (if missing)
    2. installs requirements.txt
    3. runs the test suite
    4. builds dist/PaperRadar/PaperRadar.exe        (GUI, onedir)
    5. builds dist/PaperRadar/PaperRadarCLI.exe     (batch/audit, onefile)
    6. copies README.md / requirements.txt next to the executables

.NOTES
    All messages are ASCII on purpose: Windows PowerShell 5.1 decodes .ps1 files
    with the ANSI code page unless the file has a UTF-8 BOM, which used to break
    the script when non-ASCII text was embedded.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build.ps1 -SkipTests -OfficialPyPI
#>
param(
    [switch]$SkipTests,
    [switch]$Clean,
    [switch]$OfficialPyPI,
    [string]$Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$TrustedHost = "pypi.tuna.tsinghua.edu.cn"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

Write-Step "Python environment"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment .venv ..."
    $basePython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $basePython) {
        throw "python not found. Install Python 3.11+ and add it to PATH."
    }
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }
}
& $VenvPython --version
if ($LASTEXITCODE -ne 0) { throw "The virtual environment is not usable." }

Write-Step "Installing dependencies"
$pipArgs = @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip")
if (-not $OfficialPyPI) {
    $pipArgs += @("-i", $Mirror, "--trusted-host", $TrustedHost)
}
& $VenvPython @pipArgs
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }

$pipArgs = @("-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt")
if (-not $OfficialPyPI) {
    $pipArgs += @("-i", $Mirror, "--trusted-host", $TrustedHost)
}
& $VenvPython @pipArgs
if ($LASTEXITCODE -ne 0) { throw "Failed to install requirements." }

if (-not $SkipTests) {
    Write-Step "Running tests"
    & $VenvPython -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; build aborted." }
} else {
    Write-Host "Tests skipped (-SkipTests)." -ForegroundColor Yellow
}

if ($Clean) {
    Write-Step "Cleaning previous build"
    foreach ($dir in @("build", "dist")) {
        if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
    }
}

$common = @(
    "--noconfirm", "--clean",
    "--add-data", "$Root\config;config",
    "--add-data", "$Root\database\schema.sql;database",
    "--exclude-module", "tkinter",
    "--exclude-module", "matplotlib",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    "--exclude-module", "pytest",
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.QtWebEngineQuick",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.QtQml",
    "--exclude-module", "PySide6.QtMultimedia",
    "--exclude-module", "PySide6.QtCharts",
    "--exclude-module", "PySide6.QtDataVisualization",
    "--exclude-module", "PySide6.QtBluetooth",
    "--exclude-module", "PySide6.QtNetworkAuth"
)

Write-Step "Building GUI (onedir)"
& $VenvPython -m PyInstaller @common `
    --name PaperRadar `
    --windowed `
    --distpath dist `
    --workpath build/gui `
    --specpath build `
    main.py
if ($LASTEXITCODE -ne 0) { throw "GUI build failed." }

Write-Step "Building CLI (onedir, for batch audits)"
& $VenvPython -m PyInstaller @common `
    --name PaperRadarCLI `
    --console `
    --distpath dist/cli `
    --workpath build/cli `
    --specpath build `
    tools/cli_entry.py
if ($LASTEXITCODE -ne 0) { throw "CLI build failed." }

Write-Step "Collecting artifacts"
$guiDir = Join-Path $Root "dist\PaperRadar"
$cliDir = Join-Path $guiDir "cli"
if (Test-Path $cliDir) { Remove-Item $cliDir -Recurse -Force }
if (Test-Path (Join-Path $Root "dist\cli\PaperRadarCLI")) {
    Move-Item (Join-Path $Root "dist\cli\PaperRadarCLI") $cliDir
}
Copy-Item (Join-Path $Root "README.md") $guiDir -Force
Copy-Item (Join-Path $Root "requirements.txt") $guiDir -Force
if (Test-Path (Join-Path $Root "dist\cli")) {
    Remove-Item (Join-Path $Root "dist\cli") -Recurse -Force
}

$exe = Join-Path $guiDir "PaperRadar.exe"
$cliExe = Join-Path $cliDir "PaperRadarCLI.exe"
if (-not (Test-Path $exe)) { throw "Expected executable was not produced: $exe" }
if (-not (Test-Path $cliExe)) { throw "Expected CLI executable was not produced: $cliExe" }

Write-Step "Done"
$size = [math]::Round(((Get-ChildItem $guiDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "GUI : $exe" -ForegroundColor Green
Write-Host "CLI : $cliExe" -ForegroundColor Green
Write-Host "Size: $size MB"
Write-Host ""
Write-Host "The first run creates library/ database/ logs/ config/ next to the exe."
Write-Host "Self-check: .\dist\PaperRadar\PaperRadar.exe --smoke smoke.png"
Write-Host "Batch audit: .\dist\PaperRadar\cli\PaperRadarCLI.exe audit --titles titles.txt --out audit.csv --resume"
