# Deepgram CLI installer for Windows
# Usage:
#   iwr https://deepgram.com/install.ps1 -useb | iex
#   $env:DEEPCTL_VERSION='0.2.1'; iwr https://deepgram.com/install.ps1 -useb | iex
#   $env:DEEPCTL_FORCE='1'; iwr https://deepgram.com/install.ps1 -useb | iex

$ErrorActionPreference = 'Stop'

$Package = 'deepctl'
$Version = if ($env:DEEPCTL_VERSION) { $env:DEEPCTL_VERSION } else { '' }
$Force = if ($env:DEEPCTL_FORCE -eq '1') { $true } else { $false }
$Method = ''

# Clean up env vars so they don't persist
$env:DEEPCTL_VERSION = $null
$env:DEEPCTL_FORCE = $null

function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# throw, not exit: under `iwr ... | iex` exit would close the user's window
# before the error can be read. Run as a file, an uncaught throw still exits 1.
function Stop-Install($Message) {
    throw "Error: $Message"
}

# The uv installer is pinned to one release and its SHA256: it is downloaded,
# verified, and only then run, so a changed installer never runs.
# Bump all three together (the SHA256 is the release's uv-installer.ps1 asset).
$UvVersion = '0.12.24'
$UvInstallerSha256 = '05CFB8CCA7FB43455B3EFE78C9FAB72189A97491E9FADA5A3FA621F1AFB9B3F4'
# A mirror may serve the same file; it must still match $UvInstallerSha256.
$UvInstallerUrl = if ($env:DEEPCTL_UV_INSTALLER_URL) { $env:DEEPCTL_UV_INSTALLER_URL } else { "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-installer.ps1" }
$UvManual = 'install uv yourself (https://docs.astral.sh/uv/), then run this script again.'

function Install-Uv {
    Write-Host "Installing uv ${UvVersion}..."
    $UvInstaller = Join-Path ([System.IO.Path]::GetTempPath()) ("uv-installer-" + [guid]::NewGuid().ToString('N') + '.ps1')
    try {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $UvInstallerUrl -OutFile $UvInstaller
        }
        catch {
            Stop-Install "Could not download the uv installer from ${UvInstallerUrl}. Check your connection, or $UvManual"
        }
        $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $UvInstaller).Hash
        if ($Actual -ne $UvInstallerSha256) {
            Stop-Install "The uv installer from ${UvInstallerUrl} does not match the SHA256 pinned for uv ${UvVersion}, so it was not run. If you set DEEPCTL_UV_INSTALLER_URL, check that it serves uv ${UvVersion}'s uv-installer.ps1; otherwise $UvManual"
        }
        # Run it with this engine's own console shell from $PSHOME, never the
        # host process (in PowerShell ISE that is an editor, which opens -File
        # in a tab and returns at once). & waits for a console program.
        $ShellName = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
        if ($PSVersionTable.PSEdition -ne 'Core' -or $IsWindows) { $ShellName += '.exe' }
        $Shell = Join-Path $PSHOME $ShellName
        & $Shell -NoProfile -ExecutionPolicy Bypass -File $UvInstaller
        if ($LASTEXITCODE -ne 0) {
            Stop-Install "The uv installer failed (exit ${LASTEXITCODE}). See its output above, or $UvManual"
        }
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $UvInstaller
    }
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    if (-not (Test-Command 'uv')) {
        Write-Error 'Failed to install uv. Please install manually: https://docs.astral.sh/uv/'
        exit 1
    }
}

# --- Main ---

Write-Host ''
Write-Host '  Deepgram CLI Installer'
Write-Host '  ======================'
Write-Host ''

$Spec = if ($Version) { "${Package}==${Version}" } else { $Package }

if ($Version) { Write-Host "Version: $Version" }
if ($Force) { Write-Host 'Force: reinstall' }
if ($Version -or $Force) { Write-Host '' }

$ForceFlag = if ($Force) { '--force' } else { $null }
# -U makes the upgrade explicit (pip >= 20.3 already upgrades with --force-reinstall).
$PipForceFlags = if ($Force) { @('-U', '--force-reinstall') } else { @() }

if (Test-Command 'uv') {
    Write-Host "Found uv, installing ${Spec}..."
    & uv tool install $ForceFlag $Spec
    $Method = 'uv_tool'
}
elseif (Test-Command 'pipx') {
    Write-Host "Found pipx, installing ${Spec}..."
    & pipx install $(if ($Force) { '--force' } else { $null }) $Spec
    $Method = 'pipx'
}
elseif (Test-Command 'pip3') {
    Write-Host "Found pip3, installing ${Spec}..."
    & pip3 install --user @PipForceFlags $Spec
    $Method = 'pip'
}
elseif (Test-Command 'pip') {
    Write-Host "Found pip, installing ${Spec}..."
    & pip install --user @PipForceFlags $Spec
    $Method = 'pip'
}
else {
    Write-Host 'No Python package manager found. Installing uv first...'
    Write-Host ''
    Install-Uv
    Write-Host ''
    Write-Host "Installing ${Spec}..."
    & uv tool install $ForceFlag $Spec
    $Method = 'uv_tool'
}

Write-Host ''

# Refresh PATH so the newly installed scripts are visible in this session
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [System.Environment]::GetEnvironmentVariable('Path', 'Machine')

if (Test-Command 'deepctl') {
    Write-Host 'Deepgram CLI installed successfully!'
    Write-Host ''
    try { & deepctl --record-install-method $Method | Out-Null } catch { }
    & deepctl --version
    Write-Host ''
    Write-Host 'Available as:  dg  ·  deepctl  ·  deepgram'
    Write-Host ''
    Write-Host 'Get started:'
    Write-Host '  dg login'
    Write-Host '  dg --help'
}
else {
    Write-Host "Installation complete, but the commands are not in your PATH."
    Write-Host ''
    Write-Host 'Restart your terminal to pick up the updated PATH, then run:'
    Write-Host '  dg --help'
}
