# QA Copilot launcher for Windows. The line-by-line counterpart of
# packaging/launcher/qa-copilot-launch; keep the two in step.
#
# Targets Windows PowerShell 5.1, which ships with Windows 10 and later, so it
# avoids PowerShell 7 syntax (no ternaries, no ??, no -ErrorAction on natives).
#
# ASCII ONLY, and saved with a UTF-8 BOM. Windows PowerShell 5.1 reads a
# BOM-less script as Windows-1252, so a UTF-8 em dash arrives as three
# characters ending in a curly quote - which PowerShell accepts as a string
# delimiter. One dash in a comment-free log line was enough to end the string
# early and fail the whole file to parse.
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
if (-not $homeDir) {
    # Not $env:USERPROFILE: Join-Path throws if it is unset, and this process is
    # started by another application with an environment we do not control.
    $profileDir = [Environment]::GetFolderPath('UserProfile')
    if (-not $profileDir) { $profileDir = $env:USERPROFILE }
    if (-not $profileDir) { $profileDir = "$env:HOMEDRIVE$env:HOMEPATH" }
    if (-not $profileDir) { Stop-WithError 'cannot work out your home folder' }
    $homeDir = Join-Path $profileDir '.qa-copilot'
}
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

# --- 1. an interpreter ------------------------------------------------------
# Prefer a Python that is already on the machine. Windows very often has one,
# and downloading a runtime installer to reach an interpreter that is already
# there is both slow and an extra thing to go wrong - it went wrong: a uv.exe
# that had been written but was not a runnable image failed with "cannot run a
# document in the middle of a pipeline", and nothing was catching it.

function Test-Runnable([string]$exe) {
    # A file can exist, and still not be something Windows will execute.
    try {
        & $exe --version 2>&1 | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-PythonCandidates {
    <#
      Find every Python this machine has, without trusting PATH.

      PATH is the trap. Claude Desktop logs the PATH it resolved for itself,
      which included ...\Programs\Python\Python314 - but the powershell.exe it
      spawns did not have it, and Get-Command found no python at all. A per-user
      Python install writes to the *user* PATH, and a child process started from
      a differently-scoped environment does not see it.

      So: ask the registry, which is where installers record themselves, look in
      the standard install directories, and only then fall back to PATH.
    #>
    $found = New-Object System.Collections.Generic.List[string]

    function Add-Candidate([string]$path) {
        if (-not $path) { return }
        if (-not (Test-Path $path -PathType Leaf)) { return }
        # WindowsApps holds zero-byte execution-alias stubs that open the Store.
        if ($path -like '*\WindowsApps\*') { return }
        foreach ($existing in $found) {
            if ($existing -ieq $path) { return }
        }
        $found.Add($path)
    }

    # 1. The registry, where Python's installer records InstallPath.
    foreach ($root in @('HKCU:\Software\Python\PythonCore',
                        'HKLM:\Software\Python\PythonCore',
                        'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        try {
            Get-ChildItem $root -ErrorAction Stop | ForEach-Object {
                try {
                    $dir = (Get-ItemProperty (Join-Path $_.PSPath 'InstallPath') `
                            -ErrorAction Stop).'(default)'
                    Add-Candidate (Join-Path $dir 'python.exe')
                } catch { }
            }
        } catch { }
    }

    # 2. The usual install directories, newest first.
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA 'Programs\Python') }
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    if (${env:ProgramFiles(x86)}) { $roots += ${env:ProgramFiles(x86)} }
    foreach ($root in $roots) {
        try {
            Get-ChildItem $root -Directory -Filter 'Python3*' -ErrorAction Stop |
                Sort-Object Name -Descending |
                ForEach-Object { Add-Candidate (Join-Path $_.FullName 'python.exe') }
        } catch { }
    }

    # 3. PATH, last, because it is the thing that was missing.
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $source = $cmd.Source
            if (-not $source) { $source = $cmd.Path }
            Add-Candidate $source
        }
    }

    $out = @()
    foreach ($exe in $found) {
        $out += [pscustomobject]@{ Exe = $exe; Selector = '' }
    }

    # The py launcher can pick an install none of the above located, so keep it
    # as a last resort, with an explicit version selector.
    # Built one at a time, and only from variables that are set: Join-Path
    # throws on a null path, and a stripped environment is the situation this
    # whole function exists to survive.
    $launchers = @()
    if ($env:LOCALAPPDATA) {
        $launchers += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe')
    }
    if ($env:WINDIR) { $launchers += (Join-Path $env:WINDIR 'py.exe') }
    foreach ($launcher in $launchers) {
        if (Test-Path $launcher -PathType Leaf) {
            $out += [pscustomobject]@{ Exe = $launcher; Selector = '-3' }
            break
        }
    }
    $onPath = Get-Command py -ErrorAction SilentlyContinue
    if ($onPath -and $onPath.Source) {
        $out += [pscustomobject]@{ Exe = $onPath.Source; Selector = '-3' }
    }

    return $out
}

function New-Environment([string]$target) {
    <#
      Create the virtualenv with whatever Python this machine already has.

      Rather than probing versions first and then using the winner, this asks
      each candidate to build the environment and then asks the environment
      itself how old it is. Probing was the fragile part: a machine with Python
      3.14 on its PATH still reported nothing usable, and the code said only
      "install Python", which was both wrong and unactionable.

      Every attempt is logged. A launcher that fails silently costs a release
      to diagnose.
    #>
    $candidates = @(Get-PythonCandidates)
    if ($candidates.Count -eq 0) {
        Write-Log '  found no Python in the registry, the usual install folders, or PATH'
        Write-Log ('  PATH seen by this process: ' + "$env:PATH")
        return $false
    }
    foreach ($candidate in @($candidates)) {
        $exe = $candidate.Exe
        $selector = $candidate.Selector
        $shown = if ($selector) { "$exe $selector" } else { $exe }
        Write-Log "  trying $shown"
        try {
            if ($selector) {
                & $exe $selector -m venv $target 2>&1 |
                    ForEach-Object { [Console]::Error.WriteLine("    $_") }
            } else {
                & $exe -m venv $target 2>&1 |
                    ForEach-Object { [Console]::Error.WriteLine("    $_") }
            }
        } catch {
            Write-Log "  $shown could not run: $($_.Exception.Message)"
            continue
        }
        # Both layouts, so this function can be exercised off Windows. None of
        # this has been testable before now, and that has cost four releases.
        $created = Join-Path $target 'Scripts\python.exe'
        if (-not (Test-Path $created -PathType Leaf)) {
            $created = Join-Path $target 'bin/python'
        }
        if (-not (Test-Path $created -PathType Leaf)) {
            Write-Log "  $shown did not produce an environment"
            continue
        }
        # Ask the environment itself, so there is no second guess about which
        # interpreter ended up inside it.
        $ver = ''
        try {
            $ver = & $created -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>&1
        } catch { }
        $parts = "$ver".Trim().Split('.')
        $major = 0; $minor = 0
        if ($parts.Count -ge 2) {
            [void][int]::TryParse($parts[0], [ref]$major)
            [void][int]::TryParse($parts[1], [ref]$minor)
        }
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
            Write-Log "  using Python $major.$minor from $shown"
            return $true
        }
        Write-Log "  $shown is Python $major.$minor; 3.11 or later is needed"
        Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
    }
    return $false
}

function Find-Uv {
    foreach ($candidate in @(
        (Join-Path $runtime 'bin\uv.exe'),
        (Join-Path $runtime 'uv.exe')
    )) {
        if ((Test-Path $candidate -PathType Leaf) -and (Test-Runnable $candidate)) {
            return $candidate
        }
    }
    $onPath = Get-Command uv -ErrorAction SilentlyContinue
    if ($onPath -and (Test-Runnable $onPath.Source)) { return $onPath.Source }
    return $null
}

# --- 2. the environment -----------------------------------------------------
if (-not (Test-Path $py -PathType Leaf)) {
    Write-Log 'first run: preparing a private environment'
    if (-not (New-Environment $venv)) {
        # Nothing usable is installed, so fetch a runtime that brings its own.
        $uv = Find-Uv
        if (-not $uv) {
            foreach ($stale in @((Join-Path $runtime 'bin\uv.exe'), (Join-Path $runtime 'uv.exe'))) {
                if (Test-Path $stale -PathType Leaf) {
                    # It is present and will not run; leaving it there means the
                    # installer writes next to a file that already failed.
                    Write-Log '  discarding a uv that will not run'
                    Remove-Item -Force $stale -ErrorAction SilentlyContinue
                }
            }
            Write-Log 'fetching the uv runtime installer (a few seconds)'
            New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'bin') | Out-Null
            $env:UV_UNMANAGED_INSTALL = Join-Path $runtime 'bin'
            try {
                Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            } catch {
                Stop-WithError "could not install uv: $($_.Exception.Message)"
            }
            $uv = Find-Uv
        }
        if (-not $uv) {
            Stop-WithError ("no usable Python was found and uv will not run on this " +
                            "machine. Install Python 3.11 or later from python.org, " +
                            "tick 'Add python.exe to PATH', then restart Claude Desktop.")
        }
        Write-Log 'preparing a private Python 3.12 (about a minute)'
        try {
            & $uv venv --python 3.12 $venv 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
        } catch {
            Stop-WithError "could not create the Python environment: $($_.Exception.Message)"
        }
    }
    if (-not (Test-Path $py -PathType Leaf)) {
        Stop-WithError "the Python environment was not created at $venv"
    }
}

$installed = ''
if (Test-Path $stamp) { $installed = (Get-Content $stamp -Raw).Trim() }
if ($installed -ne $version) {
    Write-Log "installing QA Copilot $version"
    try {
        & $py -m pip install --quiet --upgrade pip 2>&1 | Out-Null
        & $py -m pip install --quiet $src 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
    } catch {
        Stop-WithError "could not install QA Copilot's dependencies: $($_.Exception.Message)"
    }
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
        Write-Log "could not finish setup for '$envName' - the tools still work against"
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
