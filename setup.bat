@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  Bracket Ranker - Setup and Launch
echo ============================================
echo.

REM --- Step 1: Check for Python ---
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH. Install Python 3.11+ and try again.
    pause
    exit /b 1
)

REM --- Step 2: Create virtual environment if it doesn't exist yet ---
set "FIRST_TIME=0"
if not exist ".venv\Scripts\activate.bat" (
    echo [SETUP] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    set "FIRST_TIME=1"
) else (
    echo [OK] Virtual environment already exists.
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

REM --- Step 3: Install dependencies if this is a new venv, or if they're missing ---
if "%FIRST_TIME%"=="1" (
    echo [SETUP] Installing dependencies...
    pip install -r requirements.txt || goto :install_error
    pip install -e . || goto :install_error
) else (
    python -c "import bracket_ranker" >nul 2>&1
    if errorlevel 1 (
        echo [SETUP] Dependencies missing or incomplete, installing...
        pip install -r requirements.txt || goto :install_error
        pip install -e . || goto :install_error
    ) else (
        echo [OK] Dependencies already installed.
    )
)
goto :check_collection

:install_error
echo [ERROR] Dependency installation failed. See output above.
pause
exit /b 1

:check_collection
REM --- Step 4: Check for the ManaBox collection export ---
if not exist "ManaBox_Collection.csv" (
    echo.
    echo [WARNING] ManaBox_Collection.csv not found at the project root.
    echo           Export your collection from ManaBox and place it here, then re-run this script.
    echo.
    pause
    exit /b 1
)

REM --- Step 5: First-time full pipeline if no ranking output exists yet ---
if not exist "data\reports\ranking.csv" (
    echo.
    echo [SETUP] No ranking output found - running the full first-time pipeline.
    echo         This can take a while ^(Scryfall/Spellbook/Archidekt downloads^).
    echo.
    python -m bracket_ranker.scryfall    || goto :pipeline_error
    python -m bracket_ranker.spellbook   || goto :pipeline_error
    python -m bracket_ranker.collection  || goto :pipeline_error
    python run_ingest.py                 || goto :pipeline_error
    python -m bracket_ranker.resolve     || goto :pipeline_error
    python run_analyze.py                || goto :pipeline_error
    python run_calibrate.py              || goto :pipeline_error
    python run_export.py                 || goto :pipeline_error
    goto :launch
)

REM --- Step 6: Already set up - only refresh if the collection export changed ---
echo [OK] Existing data found. Checking whether ManaBox_Collection.csv has changed...

set "NEEDS_UPDATE=0"
for /f %%A in ('powershell -NoProfile -Command "if ((Get-Item 'ManaBox_Collection.csv').LastWriteTime -gt (Get-Item 'data\reports\ranking.csv').LastWriteTime) { Write-Output 1 } else { Write-Output 0 }"') do set "NEEDS_UPDATE=%%A"

if "%NEEDS_UPDATE%"=="1" (
    echo [UPDATE] ManaBox_Collection.csv is newer than the last ranking - refreshing collection-dependent steps...
    python -m bracket_ranker.collection  || goto :pipeline_error
    python -m bracket_ranker.resolve     || goto :pipeline_error
    python run_analyze.py                || goto :pipeline_error
    python run_calibrate.py              || goto :pipeline_error
    python run_export.py                 || goto :pipeline_error
) else (
    echo [OK] Data is already up to date.
)

:launch
echo.
echo [LAUNCH] Starting the web UI...
start /B python run_webapp.py
timeout /t 3 /nobreak >nul
start "" http://localhost:5050
echo.
echo Server is running in this window. Close it, or press Ctrl+C, to stop.
echo.
pause >nul
exit /b 0

:pipeline_error
echo.
echo [ERROR] A pipeline step failed. See the output above for details.
pause
exit /b 1
