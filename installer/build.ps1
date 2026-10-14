<#
.SYNOPSIS
    Build ArchPilot into a packaged executable and, if Inno Setup is available, an installer.

.DESCRIPTION
    Runs PyInstaller using installer\archpilot.spec to produce dist\ArchPilot\ArchPilot.exe.
    If the Inno Setup compiler (iscc.exe) can be found, it then compiles
    installer\archpilot.iss into installer\Output\ArchPilot-Setup-<version>.exe.

    Both steps report clearly whether they ran; the script does not pretend an installer was
    produced when Inno Setup is not installed.

.EXAMPLE
    powershell -File installer\build.ps1

.EXAMPLE
    pwsh -File installer\build.ps1 -SkipInstaller
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller
)

function Invoke-Native {
    <#
    .SYNOPSIS
        Run an external program and return its exit code.
    .DESCRIPTION
        With $ErrorActionPreference = 'Stop', Windows PowerShell 5.1 turns anything a native
        command writes to stderr into a terminating error. PyInstaller writes its ordinary
        progress messages to stderr, so the build stopped on its very first line. The preference
        is relaxed for the duration of the call only. The program's own output is shown rather
        than captured, and only its exit code is returned; callers still fail loudly on a
        non-zero code, which is what indicates a real failure.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Show the tool's progress, but do not let it become this function's return value.
        # A tool that logs to stdout (ISCC does) otherwise ends up returned *alongside* the exit
        # code, and the caller compares an array of log lines against 0 and reports a failure for
        # a build that actually succeeded. 2>&1 keeps stderr from terminating the script too.
        & $FilePath @Arguments 2>&1 | Write-Host
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "Building the application with PyInstaller..." -ForegroundColor Cyan
    $code = Invoke-Native python @("-m", "PyInstaller", "installer\archpilot.spec", "--noconfirm")
    if ($code -ne 0) { throw "PyInstaller failed with exit code $code." }

    $exe = Join-Path $root "dist\ArchPilot\ArchPilot.exe"
    if (-not (Test-Path $exe)) { throw "Expected $exe but it was not produced." }
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "Built $exe ($size MB)." -ForegroundColor Green

    if ($SkipInstaller) {
        Write-Host "Skipping the installer step (-SkipInstaller)." -ForegroundColor Yellow
        return
    }

    # Find iscc.exe. On PATH it is an ApplicationInfo whose full path is .Source; a direct
    # hit in the usual install folders is the path itself. Note ${env:ProgramFiles(x86)}: the
    # braces are required, because PowerShell would otherwise read "$env:ProgramFiles" and
    # leave a literal "(x86)" behind.
    $isccPath = $null
    $isccCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($isccCommand) {
        $isccPath = $isccCommand.Source
    }
    if (-not $isccPath) {
        foreach ($candidate in @(
                "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $candidate) { $isccPath = $candidate; break }
        }
    }
    if (-not $isccPath) {
        Write-Host "Inno Setup was not found, so no installer was produced." -ForegroundColor Yellow
        Write-Host "Install it from https://jrsoftware.org/isinfo.php and re-run, or compile"
        Write-Host "installer\archpilot.iss manually."
        return
    }

    Write-Host "Compiling the installer with Inno Setup ($isccPath)..." -ForegroundColor Cyan
    $code = Invoke-Native $isccPath @("installer\archpilot.iss")
    if ($code -ne 0) { throw "Inno Setup failed with exit code $code." }
    Write-Host "Installer written to installer\Output." -ForegroundColor Green
}
finally {
    Pop-Location
}
