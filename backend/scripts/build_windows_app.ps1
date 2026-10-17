<#
.SYNOPSIS
    Builds the packaged Windows desktop app: AI Video Enhancer.exe

.DESCRIPTION
    1. Exports the Next.js frontend to static HTML (frontend/out)
    2. Copies ffmpeg.exe + ffprobe.exe into backend/packaging/bin
    3. Runs PyInstaller with backend/packaging/app.spec
    Result: dist/AI Video Enhancer/AI Video Enhancer.exe
    Double-click it and the app opens in a native window - no terminal,
    no browser, no Node.js needed on the user's machine.

.PARAMETER CpuOnly
    Skip the CUDA/cuDNN DLLs (~2.3 GB smaller, but ~10x slower on a gaming GPU).

.PARAMETER SkipUi
    Reuse an existing frontend/out instead of rebuilding the web UI.

.PARAMETER Clean
    Remove previous build artifacts before starting.

.PARAMETER OutDir
    Where to place the packaged app. Defaults to backend\dist. Useful when dist
    is in use by a running instance (Windows cannot overwrite a running exe),
    e.g. -OutDir dist_fixed.

.EXAMPLE
    .\build_windows_app.ps1
    .\build_windows_app.ps1 -Clean
    .\build_windows_app.ps1 -CpuOnly
    .\build_windows_app.ps1 -OutDir dist_fixed
#>
[CmdletBinding()]
param(
    [switch]$CpuOnly,
    [switch]$SkipUi,
    [switch]$Clean,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'

$Backend = Split-Path -Parent $PSScriptRoot          # backend/
$Project = Split-Path -Parent $Backend               # project root
$Frontend = Join-Path $Project 'frontend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'
$PkgDir = Join-Path $Backend 'packaging'
$UiOut = Join-Path $Frontend 'out'
if ($OutDir) {
    $DistDir = if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $Backend $OutDir }
} else {
    $DistDir = Join-Path $Backend 'dist'
}
$ExePath = Join-Path $DistDir 'AI Video Enhancer\AI Video Enhancer.exe'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "WARNING: $msg" -ForegroundColor Yellow }

if (-not (Test-Path $VenvPython)) {
    throw "Backend venv not found at $VenvPython - create it first (see README: python -m venv .venv)."
}

# ---------------------------------------------------------------- 1. build deps
Step 'Checking build dependencies'
& $VenvPython -m pip install --quiet --disable-pip-version-check pyinstaller pywebview
if ($LASTEXITCODE -ne 0) { throw 'Failed to install pyinstaller/pywebview into the backend venv.' }
Write-Host 'pyinstaller + pywebview ready'

$nodeOk = (Get-Command node -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue)
if (-not $nodeOk -and -not $SkipUi) {
    throw 'node/npm not found on PATH. Install Node.js, or rerun with -SkipUi to reuse a previous frontend/out.'
}

# --------------------------------------------------------------- 2. static UI
if ($SkipUi) {
    if (-not (Test-Path (Join-Path $UiOut 'index.html'))) {
        throw "-SkipUi was given but $UiOut has no index.html. Run without -SkipUi once."
    }
    Step 'Reusing existing frontend/out'
} else {
    if (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue) {
        Warn 'Something is listening on port 3000 (next dev?). `next build` may fight it - stop it for a clean build.'
    }
    Step 'Exporting the web UI (static build, no Node needed at runtime)'
    Push-Location $Frontend
    try {
        if ($Clean) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '.next', 'out' }
        $env:NEXT_EXPORT = '1'
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend export build failed.' }
    } finally {
        Pop-Location
        Remove-Item Env:NEXT_EXPORT -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path (Join-Path $UiOut 'index.html'))) {
        throw "Frontend build reported success but $UiOut\index.html is missing."
    }
    $uiMb = [math]::Round((Get-ChildItem $UiOut -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
    Write-Host "UI exported ($uiMb MB)"
}

# --------------------------------------------------------- 3. FFmpeg binaries
Step 'Locating FFmpeg'
$binDir = Join-Path $PkgDir 'bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$paths = & $VenvPython -c "import sys; sys.path.insert(0, r'$Backend'); from app.config import FFMPEG, FFPROBE; print(FFMPEG); print(FFPROBE)"
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve FFmpeg paths through app.config.' }
foreach ($line in $paths) {
    if (-not $line) { continue }
    if (Test-Path $line) {
        Copy-Item $line -Destination $binDir -Force
        Write-Host ("  bundled: " + $line)
    } else {
        Warn "not found: $line - the app will search PATH on the user's machine"
    }
}

# ----------------------------------------------------------------- 4. PyInstaller
Step 'Running PyInstaller (takes a few minutes; bundles ~2.3 GB of CUDA DLLs)'
if ($Clean) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Backend 'build'), $DistDir }
if ($CpuOnly) { $env:AVE_CPU_ONLY = '1' } else { Remove-Item Env:AVE_CPU_ONLY -ErrorAction SilentlyContinue }
& $VenvPython -m PyInstaller (Join-Path $PkgDir 'app.spec') --noconfirm --distpath $DistDir --workpath (Join-Path $Backend 'build')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

if (-not (Test-Path $ExePath)) { throw "Build finished but $ExePath is missing." }

$sizeMb = [math]::Round((Get-ChildItem (Split-Path $ExePath) -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 0)
Write-Host "`nBuild complete: $ExePath" -ForegroundColor Green
Write-Host "Total size: $sizeMb MB  (includes FFmpeg, the RIFE models and $(if ($CpuOnly) {'CPU-only runtime'} else {'the CUDA runtime'}))"

if (-not $CpuOnly) {
    Write-Host @"

Note: users only need the WebView2 Runtime (preinstalled on Windows 10/11) and an
NVIDIA GPU driver - Python, Node.js and FFmpeg are all bundled inside.
"@
}
