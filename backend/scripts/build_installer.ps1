<#
.SYNOPSIS
    Compiles the Windows installer from the packaged desktop app.

.DESCRIPTION
    Turns backend\dist\AI Video Enhancer into a normal Windows setup program:

        AI-Video-Enhancer-Setup-<version>.exe

    which walks a first-time user through

        Welcome -> License (I accept) -> Choose folder -> Install -> Finish

    and registers the app in Settings > Apps with an uninstaller.

    Requires Inno Setup 6 (free):
        winget install --id JRSoftware.InnoSetup -e --scope user

.PARAMETER Version
    Version stamped on the installer and shown in Settings > Apps.

.PARAMETER SourceDir
    Packaged app to package. Defaults to backend\dist\AI Video Enhancer, so run
    build_windows_app.ps1 first.

.PARAMETER OutDir
    Where the setup .exe is written. Defaults to backend\installer_output.

.EXAMPLE
    .\build_installer.ps1
    .\build_installer.ps1 -Version 1.1.0
    .\build_installer.ps1 -SourceDir "D:\portable\AI Video Enhancer" -OutDir "C:\drop"
#>
[CmdletBinding()]
param(
    [string]$Version = '1.0.0',
    [string]$SourceDir = '',
    [string]$OutDir = '',
    [string]$OutputBase = 'AI-Video-Enhancer-Setup'
)

$Backend = Split-Path -Parent $PSScriptRoot          # backend/
$Project = Split-Path -Parent $Backend               # project root
$IssFile = Join-Path $Backend 'packaging\installer.iss'

if (-not $SourceDir) { $SourceDir = Join-Path $Backend 'dist\AI Video Enhancer' }
if (-not $OutDir) { $OutDir = Join-Path $Backend 'installer_output' }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 1. toolchain
Step 'Locating Inno Setup 6 (ISCC.exe)'
$candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Inno Setup 6\ISCC.exe'),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$Iscc = $null
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { $Iscc = $candidate; break }
}
if (-not $Iscc) {
    $fromPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($fromPath) { $Iscc = $fromPath.Source }
}
if (-not $Iscc) {
    throw 'Inno Setup 6 not found. Install it first:  winget install --id JRSoftware.InnoSetup -e --scope user'
}
Write-Host "  $Iscc"

# ------------------------------------------------------------- 2. source check
if (-not (Test-Path (Join-Path $SourceDir 'AI Video Enhancer.exe'))) {
    throw "No packaged app at '$SourceDir'. Run build_windows_app.ps1 first, or pass -SourceDir."
}
$sourceMb = [math]::Round((Get-ChildItem $SourceDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 0)
Write-Host "  packaging $sourceMb MB from $SourceDir"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$LogFile = Join-Path $OutDir 'iscc.log'
$ErrFile = Join-Path $OutDir 'iscc.err.log'
Remove-Item $LogFile, $ErrFile -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- 3. compile
Step "Compiling the installer (single-threaded LZMA over $sourceMb MB, takes a few minutes)"
$arguments = @(
    ('"{0}"' -f $IssFile),
    "/DAppVersion=$Version",
    ('/DSourceDir="{0}"' -f $SourceDir),
    ('/DOutputDir="{0}"' -f $OutDir),
    "/DOutputBase=$OutputBase"
)

$watch = [System.Diagnostics.Stopwatch]::StartNew()
# Redirect to files rather than the pipeline: PowerShell turns native stderr into
# error records, which aborts scripts running with $ErrorActionPreference = 'Stop'.
$process = Start-Process -FilePath $Iscc -ArgumentList $arguments -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile
$watch.Stop()

foreach ($file in @($LogFile, $ErrFile)) {
    if ((Test-Path $file) -and (Get-Item $file).Length -gt 0) {
        $lines = Get-Content $file | Select-Object -Last 12
        $lines | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
}
if ($process.ExitCode -ne 0) {
    throw "ISCC failed with exit code $($process.ExitCode). See $LogFile and $ErrFile."
}

$Setup = Join-Path $OutDir "$OutputBase-$Version.exe"
if (-not (Test-Path $Setup)) { throw "Build finished but $Setup is missing." }

$setupMb = [math]::Round((Get-Item $Setup).Length / 1MB, 0)
$elapsed = [math]::Round($watch.Elapsed.TotalSeconds, 0)
Write-Host "`nInstaller created: $Setup" -ForegroundColor Green
Write-Host "Size: $setupMb MB   (${elapsed}s)"
Write-Host @"


A first-time user now gets the standard Windows flow:
  download -> run -> "I accept" -> choose folder -> Install -> Finish -> Launch
"@
