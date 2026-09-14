$ErrorActionPreference = 'Stop'

Write-Host 'FoveaStream Windows setup'

$python = $null
foreach ($candidate in @('py', 'python')) {
    try {
        & $candidate --version *> $null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    } catch {}
}
if (-not $python) {
    throw 'Python 3.10+ was not found. Install Python first, then rerun this script.'
}

if ($python -eq 'py') {
    $versionText = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$versionText -lt [version]'3.10') { throw 'Python 3.10+ is required.' }
    if (-not (Test-Path '.venv')) { & py -3 -m venv .venv }
} else {
    $versionText = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$versionText -lt [version]'3.10') { throw 'Python 3.10+ is required.' }
    if (-not (Test-Path '.venv')) { & python -m venv .venv }
}

$venvPython = Join-Path $PWD '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e '.[dev]'

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'ffmpeg not found. Installing Gyan.FFmpeg with winget...'
        winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
        Write-Host 'ffmpeg was installed. Reopen PowerShell if it is not visible in PATH yet.'
    } else {
        Write-Warning 'ffmpeg is missing and winget is unavailable. Install an ffmpeg build with libx264 support and add it to PATH.'
    }
}

Write-Host ''
Write-Host 'Python environment ready.'
Write-Host 'Activate with:'
Write-Host '  .\.venv\Scripts\Activate.ps1'
Write-Host 'Run the demo with:'
Write-Host '  python bench\real_video_visualization.py example1.mp4 --outdir output --preset aggressive'
