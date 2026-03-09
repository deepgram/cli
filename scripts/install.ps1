# Deepgram CLI installer for Windows
# Usage:
#   iwr https://deepgram.com/install.ps1 -useb | iex
#   $env:DEEPCTL_VERSION='0.2.1'; iwr https://deepgram.com/install.ps1 -useb | iex
#   $env:DEEPCTL_FORCE='1'; iwr https://deepgram.com/install.ps1 -useb | iex

$ErrorActionPreference = 'Stop'

$Package = 'deepctl'
$Version = if ($env:DEEPCTL_VERSION) { $env:DEEPCTL_VERSION } else { '' }
$Force = if ($env:DEEPCTL_FORCE -eq '1') { $true } else { $false }

# Clean up env vars so they don't persist
$env:DEEPCTL_VERSION = $null
$env:DEEPCTL_FORCE = $null

function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-Uv {
    Write-Host 'Installing uv...'
    irm https://astral.sh/uv/install.ps1 | iex
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

if (Test-Command 'uv') {
    Write-Host "Found uv, installing ${Spec}..."
    & uv tool install $ForceFlag $Spec
}
elseif (Test-Command 'pipx') {
    Write-Host "Found pipx, installing ${Spec}..."
    & pipx install $(if ($Force) { '--force' } else { $null }) $Spec
}
elseif (Test-Command 'pip3') {
    Write-Host "Found pip3, installing ${Spec}..."
    & pip3 install --user $(if ($Force) { '--force-reinstall' } else { $null }) $Spec
}
elseif (Test-Command 'pip') {
    Write-Host "Found pip, installing ${Spec}..."
    & pip install --user $(if ($Force) { '--force-reinstall' } else { $null }) $Spec
}
else {
    Write-Host 'No Python package manager found. Installing uv first...'
    Write-Host ''
    Install-Uv
    Write-Host ''
    Write-Host "Installing ${Spec}..."
    & uv tool install $ForceFlag $Spec
}

Write-Host ''

if (Test-Command 'deepctl') {
    Write-Host 'Deepgram CLI installed successfully!'
    Write-Host ''
    & deepctl --version
    Write-Host ''
    Write-Host 'Get started:'
    Write-Host '  deepctl login'
    Write-Host '  deepctl --help'
}
else {
    Write-Host "Installation complete, but 'deepctl' is not in your PATH."
    Write-Host ''
    Write-Host 'You may need to restart your terminal or add the install location to your PATH.'
    Write-Host ''
    Write-Host 'Then run: deepctl --help'
}
