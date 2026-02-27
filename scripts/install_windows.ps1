$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectDir

$envName = "shortvideo-agent"
$pyVer = "3.11"

function Write-Condarc {
  Write-Host "[0/7] Writing ~/.condarc (TUNA mirrors, strict)..."
  $condarcPath = Join-Path $env:USERPROFILE ".condarc"

  $content = @"
show_channel_urls: true
channel_priority: strict
ssl_verify: true

default_channels:
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/msys2

custom_channels:
  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
  pytorch: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
  nvidia: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud

channels:
  - conda-forge
  - defaults

remote_connect_timeout_secs: 60
remote_read_timeout_secs: 180
"@

  $content | Out-File -FilePath $condarcPath -Encoding utf8
  Write-Host "----- $condarcPath -----"
  Get-Content $condarcPath | Write-Host
  Write-Host "------------------------"
}

function Ensure-Conda {
  if (Get-Command conda -ErrorAction SilentlyContinue) {
    Write-Host "[1/7] Conda already installed."
    return
  }

  Write-Host "[1/7] Installing Miniconda..."
  $miniUrl = "https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Windows-x86_64.exe"
  $installer = Join-Path $env:TEMP "miniconda.exe"
  Invoke-WebRequest -Uri $miniUrl -OutFile $installer

  $target = Join-Path $env:USERPROFILE "Miniconda3"
  Start-Process -FilePath $installer -ArgumentList "/InstallationType=JustMe /AddToPath=1 /RegisterPython=0 /S /D=$target" -Wait

  $condaExe = Join-Path $target "Scripts\conda.exe"
  if (-not (Test-Path $condaExe)) { throw "Conda install failed." }

  # Make conda available in this session
  $env:Path = (Join-Path $target "Scripts") + ";" + (Join-Path $target "Library\bin") + ";" + $env:Path
}

Write-Condarc
Ensure-Conda

# Load conda hook
$condaBase = (& conda info --base).Trim()
$condaHook = Join-Path $condaBase "shell\condabin\conda-hook.ps1"
. $condaHook

# remove base condarc (repo.anaconda.com injection)
$baseCondarc = Join-Path $condaBase ".condarc"
if (Test-Path $baseCondarc) {
  Write-Host "Found base condarc at $baseCondarc, backing up to $baseCondarc.bak"
  Copy-Item $baseCondarc "$baseCondarc.bak" -Force
  Remove-Item $baseCondarc -Force
}

Write-Host "[2/7] show-sources:"
try { conda config --show-sources } catch { Write-Host "conda config --show-sources failed: $($_.Exception.Message)" }

Write-Host "[3/7] conda base: $condaBase"
$envsDir = Join-Path $condaBase "envs"
$targetPrefix = Join-Path $envsDir $envName

Write-Host "Target env prefix: $targetPrefix"
if (-not (Test-Path $envsDir)) { New-Item -ItemType Directory -Path $envsDir | Out-Null }

# warn if env exists in other conda installs (heuristic)
$altPrefix1 = Join-Path (Join-Path $env:USERPROFILE "Miniconda3\envs") $envName
$altPrefix2 = Join-Path (Join-Path $env:USERPROFILE "Anaconda3\envs") $envName
if ((Test-Path $altPrefix1) -and ($targetPrefix -ne $altPrefix1) -and (-not (Test-Path $targetPrefix))) {
  Write-Host "Warning: Found env in Miniconda3: $altPrefix1"
  Write-Host "But current conda base is: $condaBase"
  Write-Host "You may be mixing two conda installs. This script will create env under current base."
}
if ((Test-Path $altPrefix2) -and ($targetPrefix -ne $altPrefix2) -and (-not (Test-Path $targetPrefix))) {
  Write-Host "Warning: Found env in Anaconda3: $altPrefix2"
}

Write-Host "[4/7] Creating/ensuring env: $envName"
if (Test-Path $targetPrefix) {
  Write-Host "Env directory exists: $targetPrefix"
} else {
  try { conda clean --all -y } catch {}
  conda create -y -n $envName python=$pyVer pip
}

Write-Host "[5/7] Activating env..."
conda activate $envName

Write-Host "[6/7] Installing project..."
python -m pip install -U pip
pip install -e .

Write-Host "[7/7] Installing ffmpeg if missing..."
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Installing ffmpeg via conda-forge..."
  conda install -y -c conda-forge ffmpeg
}

Write-Host "Done."
Write-Host "Activate: conda activate $envName"
Write-Host "Run: shortvideo-agent --help"
Write-Host "Or: powershell -ExecutionPolicy Bypass -File scripts/run_windows.ps1"