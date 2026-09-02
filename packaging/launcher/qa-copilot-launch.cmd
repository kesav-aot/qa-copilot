@echo off
rem QA Copilot launcher for Windows.
rem
rem Batch, not PowerShell, and deliberately. Every Windows failure so far came
rem from PowerShell's assumptions about the environment Claude Desktop gives it:
rem a stripped PATH so no interpreter was found, no PATHEXT so a valid .exe was
rem treated as a document and refused, argument quoting that silently dropped
rem anything containing a space, and an encoding rule that turned one dash into
rem a parse error. cmd.exe starts programs directly and has none of that.
rem
rem STDOUT IS THE MCP CHANNEL. Every message here goes to stderr with 1>&2.

setlocal EnableDelayedExpansion

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

rem A host leaves ${user_config.x} in place when the field is blank. Treat a
rem leftover placeholder as unset rather than as a literal value.
call :clean QA_COPILOT_HOME
call :clean QA_COPILOT_CONFIG
call :clean QA_COPILOT_STATE
call :clean QA_COPILOT_APP_URL
call :clean QA_COPILOT_ENV_NAME

rem --- which layout is this ---------------------------------------------------
if exist "%ROOT%\src\pyproject.toml" (
    set "SRC=%ROOT%\src"
) else if exist "%ROOT%\pyproject.toml" (
    set "SRC=%ROOT%"
) else (
    echo qa-copilot: cannot find the QA Copilot package under %ROOT% 1>&2
    exit /b 1
)

if exist "%ROOT%\config-defaults" (
    set "DEFAULTS=%ROOT%\config-defaults"
) else if exist "%ROOT%\packaging\config-defaults" (
    set "DEFAULTS=%ROOT%\packaging\config-defaults"
) else (
    set "DEFAULTS=%ROOT%\config"
)

set "VERSION="
if exist "%ROOT%\VERSION" set /p VERSION=<"%ROOT%\VERSION"
if not defined VERSION (
    for /f "tokens=2 delims== " %%V in ('findstr /b /c:"version = " "%SRC%\pyproject.toml"') do (
        if not defined VERSION set "VERSION=%%~V"
    )
)
if not defined VERSION set "VERSION=dev"

if not defined QA_COPILOT_HOME set "QA_COPILOT_HOME=%USERPROFILE%\.qa-copilot"
set "HOMEDIR=%QA_COPILOT_HOME%"
set "RUNTIME=%HOMEDIR%\runtime"
set "VENV=%RUNTIME%\venv"
set "PY=%VENV%\Scripts\python.exe"
set "SERVER=%VENV%\Scripts\qa-copilot-mcp.exe"
set "STAMP=%RUNTIME%\installed-version"
set "BROWSERLOG=%HOMEDIR%\browser-install.log"
if not exist "%RUNTIME%" md "%RUNTIME%" 2>nul

rem --- one provisioner at a time ---------------------------------------------
rem md fails when the directory exists, so it is the lock.
set "LOCK=%RUNTIME%\setup.lock"
set "HAVELOCK="
set /a TRIES=0
:lock
md "%LOCK%" 2>nul && (set "HAVELOCK=1" & goto :locked)
set /a TRIES+=1
if %TRIES%==1 echo qa-copilot: another QA Copilot is setting up; waiting for it 1>&2
if %TRIES% GEQ 150 (
    echo qa-copilot: giving up waiting; clearing the lock 1>&2
    rd /s /q "%LOCK%" 2>nul
    md "%LOCK%" 2>nul && set "HAVELOCK=1"
    goto :locked
)
rem Two seconds, without needing timeout.exe, which is absent in some images.
ping -n 3 127.0.0.1 >nul 2>nul
goto :lock
:locked

rem --- the environment -------------------------------------------------------
if not exist "%PY%" (
    echo qa-copilot: first run: preparing a private environment 1>&2
    call :findpython
    if not defined PYEXE (
        echo qa-copilot: no Python 3.11 or later was found on this machine. 1>&2
        echo qa-copilot: install it from python.org, then restart Claude Desktop. 1>&2
        call :unlock
        exit /b 1
    )
    echo qa-copilot:   using "!PYEXE!" !PYARG! 1>&2
    "!PYEXE!" !PYARG! -m venv "%VENV%" 1>&2
    if not exist "%PY%" (
        echo qa-copilot: the environment was not created at %VENV% 1>&2
        call :unlock
        exit /b 1
    )
)

set "INSTALLED="
if exist "%STAMP%" set /p INSTALLED=<"%STAMP%"
if not "%INSTALLED%"=="%VERSION%" (
    echo qa-copilot: installing QA Copilot %VERSION% 1>&2
    "%PY%" -m pip install --quiet --disable-pip-version-check "%SRC%" 1>&2
    if errorlevel 1 (
        echo qa-copilot: could not install QA Copilot's dependencies 1>&2
        call :unlock
        exit /b 1
    )
    > "%STAMP%" echo %VERSION%
)

rem --- the workspace ---------------------------------------------------------
if not defined QA_COPILOT_CONFIG (
    set "QA_COPILOT_CONFIG=%HOMEDIR%\config"
    if not exist "!QA_COPILOT_CONFIG!" (
        echo qa-copilot: creating your workspace at %HOMEDIR% 1>&2
        md "!QA_COPILOT_CONFIG!" 2>nul
        xcopy /e /i /q /y "%DEFAULTS%\*" "!QA_COPILOT_CONFIG!\" >nul 2>nul
    )
)
if not defined QA_COPILOT_STATE set "QA_COPILOT_STATE=%HOMEDIR%\state"
if not exist "%QA_COPILOT_STATE%" md "%QA_COPILOT_STATE%" 2>nul
if not exist "%HOMEDIR%\tests" md "%HOMEDIR%\tests" 2>nul
if not exist "%HOMEDIR%\artifacts" md "%HOMEDIR%\artifacts" 2>nul

rem --- the test browser, once per version, in the background -----------------
set "BROWSERSTAMP=%RUNTIME%\browser-verified-%VERSION%"
if not exist "%BROWSERSTAMP%" (
    echo qa-copilot: checking the test browser (first time: about 500 MB, in the background) 1>&2
    start "" /b cmd /c ""%PY%" -m playwright install chromium >"%BROWSERLOG%" 2>&1"
    rem Written now rather than on success. A download that fails is repaired on
    rem demand by the executor, which reports it in plain language; retrying it
    rem on every single start is what made an earlier version fragile.
    > "%BROWSERSTAMP%" echo started
)

rem --- serve -----------------------------------------------------------------
if not exist "%SERVER%" (
    echo qa-copilot: the server is missing at %SERVER%. The install did not complete. 1>&2
    call :unlock
    exit /b 1
)
call :unlock
"%SERVER%"
exit /b %errorlevel%

rem ---------------------------------------------------------------------------
:clean
rem Blank a variable whose value still holds an unsubstituted ${...}
setlocal EnableDelayedExpansion
set "VAL=!%~1!"
if not defined VAL endlocal & goto :eof
set "STRIPPED=!VAL:${=!"
if not "!STRIPPED!"=="!VAL!" (
    endlocal & set "%~1=" & goto :eof
)
endlocal & goto :eof

:unlock
if defined HAVELOCK rd /s /q "%LOCK%" 2>nul
goto :eof

:findpython
rem Look where installers record themselves, not on PATH: Claude Desktop starts
rem this process without the user's PATH, so a per-user Python is invisible there.
set "PYEXE="
set "PYARG="

for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i "REG_SZ"') do (
    if not defined PYEXE if exist "%%B" call :checkpython "%%B" ""
)
if not defined PYEXE for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i "REG_SZ"') do (
    if not defined PYEXE if exist "%%B" call :checkpython "%%B" ""
)

if not defined PYEXE if defined LOCALAPPDATA (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do (
        if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" (
            call :checkpython "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" ""
        )
    )
)
if not defined PYEXE if defined ProgramFiles (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles%\Python3*" 2^>nul') do (
        if not defined PYEXE if exist "%ProgramFiles%\%%D\python.exe" (
            call :checkpython "%ProgramFiles%\%%D\python.exe" ""
        )
    )
)

rem The py launcher knows about installs none of the above found.
if not defined PYEXE if defined LOCALAPPDATA (
    if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
        call :checkpython "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" "-3"
    )
)
if not defined PYEXE if exist "%WINDIR%\py.exe" call :checkpython "%WINDIR%\py.exe" "-3"

rem PATH last, because on the machines that failed it held nothing.
if not defined PYEXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined PYEXE call :checkpython "%%P" ""
    )
)
goto :eof

:checkpython
rem %1 = interpreter, %2 = optional version selector. Accept it only if it runs
rem and is new enough. WindowsApps holds zero-byte stubs that open the Store.
echo %~1 | findstr /i "WindowsApps" >nul && goto :eof
set "CAND=%~1"
set "CARG=%~2"
"%CAND%" %CARG% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
    echo qa-copilot:   %CAND% %CARG% is not usable ^(missing or older than 3.11^) 1>&2
    goto :eof
)
set "PYEXE=%CAND%"
set "PYARG=%CARG%"
goto :eof
