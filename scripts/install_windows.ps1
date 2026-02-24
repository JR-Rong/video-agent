$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectDir

function Ensure-Conda {
  if (Get-Command conda -ErrorAction SilentlyContinue) {
    Write-Host "[1/4] Conda already installed."
    return
  }

  Write-Host "[1/4] Installing Miniconda..."
  $miniUrl = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
  $installer = Join-Path $env:TEMP "miniconda.exe"
  Invoke-WebRequest -Uri $miniUrl -OutFile $installer

  $target = Join-Path $env:USERPROFILE "Miniconda3"
  Start-Process -FilePath $installer -ArgumentList "/InstallationType=JustMe /AddToPath=1 /RegisterPython=0 /S /D=$target" -Wait

  $condaBat = Join-Path $target "Scripts\conda.exe"
  if (-not (Test-Path $condaBat)) { throw "Conda install failed." }

  $env:Path = (Join-Path $target "Scripts") + ";" + (Join-Path $target "Library\bin") + ";" + $env:Path
}

Ensure-Conda

# Use conda base
$condaBase = (& conda info --base).Trim()
$condaHook = Join-Path $condaBase "shell\condabin\conda-hook.ps1"
. $condaHook

$envName = "shortvideo-agent"
$pyVer = "3.11"

Write-Host "[2/4] Creating/ensuring env..."
$envList = (& conda env list) | Out-String
if ($envList -notmatch "^\s*$envName\s") {
  conda create -y -n $envName python=$pyVer pip
} else {
  Write-Host "Env exists: $envName"
}

conda activate $envName

Write-Host "[3/4] Installing dependencies..."
python -m pip install -U pip
pip install -e .

# ffmpeg (recommended)
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Installing ffmpeg via conda-forge..."
  conda install -y -c conda-forge ffmpeg
}

Write-Host "[4/4] Done."
Write-Host "Activate: conda activate $envName"
Write-Host "Run: powershell -ExecutionPolicy Bypass -File scripts/run_windows.ps1"