$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectDir

$condaBase = (& conda info --base).Trim()
. (Join-Path $condaBase "shell\condabin\conda-hook.ps1")
conda activate shortvideo-agent

shortvideo-agent --help
shortvideo-agent run --help