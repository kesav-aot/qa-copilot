# QA Copilot launcher for Windows. The line-by-line counterpart of
# packaging/launcher/qa-copilot-launch; keep the two in step.
#
# Targets Windows PowerShell 5.1, which ships with Windows 10 and later, so it
# avoids PowerShell 7 syntax (no ternaries, no ??, no -ErrorAction on natives).
#
# Claude speaks MCP over stdin/stdout, so STDOUT IS THE PROTOCOL CHANNEL. Every
# diagnostic goes to stderr via [Console]::Error, which cannot be redirected
# into the stream by mistake the way Write-Host and Write-Output can.

$ErrorActionPreference = 'Stop'

# PowerShell 5.1 still negotiates TLS 1.0 by default; astral.sh refuses it.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Log([string]$Message) {
    [Console]::Error.WriteLine("qa-copilot: $Message")
}

function Stop-WithError([string]$Message) {
    Write-Log $Message
    exit 1
}

# A host leaves ${user_config.x} unsubstituted when the field is blank. Treat a
# leftover placeholder as "not set" rather than as a literal value.
function Get-Setting([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return '' }
    if ($value.Contains('${')) { return '' }
    return $value.Trim()
}

$root = Get-Setting 'QA_COPILOT_BUNDLE'
if (-not $root) { $root = Get-Setting 'CLAUDE_PLUGIN_ROOT' }
if (-not $root) {
    $root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
    # In a source checkout the launcher lives at packaging\launcher\, so the
    # project root is one level further up than it is inside a bundle.
    if (-not (Test-Path (Join-Path $root 'pyproject.toml')) -and
        -not (Test-Path (Join-Path $root 'src'))) {
        $root = Split-Path -Parent $root
    }
}

# --- which layout are we in? ------------------------------------------------
$src = $null
if (Test-Path (Join-Path $root 'src\pyproject.toml')) {
    $src = Join-Path $root 'src'                 # packaged bundle
} elseif (Test-Path (Join-Path $root 'pyproject.toml')) {
    $src = $root                                 # source checkout / cloned plugin
} else {
    Stop-WithError "cannot find the QA Copilot package under $root"
}

$defaults = Join-Path $root 'config-defaults'
if (-not (Test-Path $defaults)) { $defaults = Join-Path $root 'config' }

$versionFile = Join-Path $root 'VERSION'
if (Test-Path $versionFile) {
    $version = (Get-Content $versionFile -Raw).Trim()
} else {
    $match = Select-String -Path (Join-Path $src 'pyproject.toml') `
        -Pattern '^version\s*=\s*"(.*)"' | Select-Object -First 1
    if ($match) { $version = $match.Matches[0].Groups[1].Value } else { $version = 'dev' }
}

$homeDir = Get-Setting 'QA_COPILOT_HOME'
if (-not $homeDir) { $homeDir = Join-Path $env:USERPROFILE '.qa-copilot' }
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null

$runtime = Join-Path $homeDir 'runtime'
$venv    = Join-Path $runtime 'venv'
$py      = Join-Path $venv 'Scripts\python.exe'
$stamp   = Join-Path $runtime 'installed-version'

# --- 0. one provisioner at a time -------------------------------------------
# A host may start a second server while the first is still installing; both
# then write the same virtualenv, and whichever reaches the server first can
# find a half-written console script and exit without a word.
$lock = Join-Path $runtime 'setup.lock'
$haveLock = $false
for ($i = 0; $i -lt 300; $i++) {
    try {
        [System.IO.Directory]::CreateDirectory($lock) | Out-Null
        if (-not (Test-Path (Join-Path $lock '.taken'))) {
            New-Item -ItemType File -Path (Join-Path $lock '.taken') -ErrorAction Stop | Out-Null
            $haveLock = $true
            break
        }
    } catch { }
    $age = (Get-Date) - (Get-Item $lock -ErrorAction SilentlyContinue).LastWriteTime
    if ($age.TotalMinutes -gt 10) {
        Write-Log 'clearing a stale setup lock'
        Remove-Item -Recurse -Force $lock -ErrorAction SilentlyContinue
        continue
    }
    if ($i -eq 0) { Write-Log 'another QA Copilot is setting up; waiting for it' }
    Start-Sleep -Seconds 1
}
function Release-Lock { if ($haveLock) { Remove-Item -Recurse -Force $lock -ErrorAction SilentlyContinue } }

# --- 1. uv ------------------------------------------------------------------
# uv provisions its own CPython. Windows may have no Python at all, and the one
# from the Microsoft Store shims in ways that break virtualenvs.
function Find-Uv {
    foreach ($candidate in @(
        (Join-Path $runtime 'bin\uv.exe'),
        (Join-Path $runtime 'uv.exe')
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    $onPath = Get-Command uv -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Log 'first run: fetching the uv runtime installer (a few seconds)'
    New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'bin') | Out-Null
    $env:UV_UNMANAGED_INSTALL = Join-Path $runtime 'bin'
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Stop-WithError "could not install uv: $($_.Exception.Message)"
    }
    $uv = Find-Uv
    if (-not $uv) { Stop-WithError "uv did not install under $runtime" }
}

# --- 2. interpreter + dependencies -----------------------------------------
if (-not (Test-Path $py)) {
    Write-Log 'first run: preparing a private Python 3.12 (about a minute)'
    & $uv venv --python 3.12 $venv 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
    if (-not (Test-Path $py)) { Stop-WithError 'could not create the Python environment' }
}

$installed = ''
if (Test-Path $stamp) { $installed = (Get-Content $stamp -Raw).Trim() }
if ($installed -ne $version) {
    Write-Log "installing QA Copilot $version"
    & $uv pip install --python $py --quiet $src 2>&1 |
        ForEach-Object { [Console]::Error.WriteLine($_) }
    if ($LASTEXITCODE -ne 0) { Stop-WithError "could not install QA Copilot's dependencies" }
    Set-Content -Path $stamp -Value $version -NoNewline
}

# --- 3. workspace -----------------------------------------------------------
# A host that already decided where config lives wins; otherwise config goes in
# the workspace and never in the bundle, which is replaced on upgrade.
if (-not (Get-Setting 'QA_COPILOT_CONFIG')) {
    $env:QA_COPILOT_CONFIG = Join-Path $homeDir 'config'
    if (-not (Test-Path $env:QA_COPILOT_CONFIG)) {
        Write-Log "creating your workspace at $homeDir"
        Copy-Item -Recurse -Path $defaults -Destination $env:QA_COPILOT_CONFIG
    }
}
if (-not (Get-Setting 'QA_COPILOT_STATE')) {
    $env:QA_COPILOT_STATE = Join-Path $homeDir 'state'
}
foreach ($dir in @($env:QA_COPILOT_STATE, (Join-Path $homeDir 'tests'), (Join-Path $homeDir 'artifacts'))) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}
$env:QA_COPILOT_HOME = $homeDir

# --- 4. point it at the QA engineer's app ----------------------------------
$appUrl = Get-Setting 'QA_COPILOT_APP_URL'
$envName = Get-Setting 'QA_COPILOT_ENV_NAME'
if (-not $envName) { $envName = 'local' }
$provisioned = Join-Path $runtime "provisioned-$envName"

if ($appUrl -and -not (Test-Path $provisioned)) {
    Write-Log 'checking the test browser (first time: about 500 MB)'
    & $py -m playwright install chromium 2>&1 |
        ForEach-Object { [Console]::Error.WriteLine($_) }

    Write-Log "looking at $appUrl to find its sign-in form"
    $provision = Join-Path $root 'bootstrap\provision.py'
    if (-not (Test-Path $provision)) {
        $provision = Join-Path $root 'packaging\mcpb\bootstrap\provision.py'
    }
    & $py $provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
    if ($LASTEXITCODE -eq 0) {
        New-Item -ItemType File -Force -Path $provisioned | Out-Null
        Write-Log "ready: environment '$envName' is configured"
    } else {
        Write-Log "could not finish setup for '$envName' — the tools still work against"
        Write-Log 'the demo app. Fix the settings and restart.'
    }
}

# --- 5. the test browser ----------------------------------------------------
# Unconditional, and never guarded by whether the cache directory exists: a
# directory left by a different Playwright version contains no browser this code
# can launch, and checking only for the directory skips the download forever.
$browserLog = Join-Path $homeDir 'browser-install.log'
$browserStamp = Join-Path $runtime "browser-verified-$version"
if (-not (Test-Path $browserStamp)) {
    Write-Log 'checking the test browser (first time: about 500 MB, in the background)'
    try {
        Start-Process -FilePath $py -ArgumentList '-m', 'playwright', 'install', 'chromium' `
            -RedirectStandardOutput $browserLog -RedirectStandardError "$browserLog.err" `
            -NoNewWindow | Out-Null
        New-Item -ItemType File -Force -Path $browserStamp | Out-Null
    } catch { }
}

# --- 6. serve ---------------------------------------------------------------
# A native executable invoked this way inherits the parent's stdio handles, so
# the MCP stream passes through untouched.
$server = Join-Path $venv 'Scripts\qa-copilot-mcp.exe'
if (-not (Test-Path $server)) {
    Release-Lock
    Stop-WithError "the server is missing at $server. The install did not complete; see $browserLog and try again."
}
Release-Lock
& $server
exit $LASTEXITCODE
