[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvRoot = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$PyprojectPath = Join-Path $ProjectRoot "pyproject.toml"
$InstallMarkerPath = Join-Path $VenvRoot ".clap-wake-up-pyproject.sha256"

function Get-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return @("python3")
    }
    throw "Python 3.10+ introuvable. Installe Python ou active un environnement avant de lancer ce script."
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    & $Command[0] @($Command | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Test-EditableInstall {
    if (-not (Test-Path $VenvPython)) {
        return $false
    }

    & $VenvPython -m pip show clap-wake-up *> $null
    return $LASTEXITCODE -eq 0
}

function Get-PyprojectHash {
    return (Get-FileHash $PyprojectPath -Algorithm SHA256).Hash
}

if (-not (Test-Path $VenvPython)) {
    $launcher = Get-PythonLauncher
    Invoke-CheckedCommand -Command ($launcher + @("-m", "venv", $VenvRoot))
}

$currentHash = Get-PyprojectHash
$storedHash = if (Test-Path $InstallMarkerPath) {
    (Get-Content $InstallMarkerPath -Raw).Trim()
} else {
    ""
}

$needsInstall = ($storedHash -ne $currentHash) -or -not (Test-EditableInstall)

Push-Location $ProjectRoot
try {
    if ($needsInstall) {
        Invoke-CheckedCommand -Command @($VenvPython, "-m", "pip", "install", "-e", ".")
        Set-Content -Path $InstallMarkerPath -Value $currentHash -Encoding ascii
    }

    & $VenvPython -m clap_wake @CommandArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
