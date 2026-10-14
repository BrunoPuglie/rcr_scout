@echo off
setlocal enabledelayedexpansion
title Setup RCR Platform Environment
cd /d "%~dp0"

echo ============================================================================
echo   Soybean Red Crown Rot (RCR) Platform - One-Time Environment Setup
echo   Smart Plant Pathology Lab - University of Illinois Urbana-Champaign
echo ============================================================================
echo.

:SCAN_FOR_CONDA
set "CONDA_BAT="

:: 1. Check if conda is already in PATH
where conda >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('where conda') do (
        if not defined CONDA_BAT (
            set "CONDA_BAT=%%I"
        )
    )
)

:: 2. Search known default Miniforge / Miniconda / Anaconda locations
set "CANDIDATE_DIRS=%USERPROFILE%\AppData\Local\miniforge3;%USERPROFILE%\miniforge3;%LOCALAPPDATA%\miniforge3;%LOCALAPPDATA%\Programs\miniforge3;%USERPROFILE%\AppData\Local\Programs\miniforge3;C:\miniforge3;C:\ProgramData\miniforge3;C:\Program Files\miniforge3;%USERPROFILE%\miniconda3;%USERPROFILE%\anaconda3;C:\miniconda3;C:\ProgramData\miniconda3;C:\Program Files\miniconda3;C:\anaconda3;C:\ProgramData\anaconda3;C:\Program Files\anaconda3"
if not defined CONDA_BAT (
    for %%D in ("%CANDIDATE_DIRS:;=" "%") do (
        if not defined CONDA_BAT (
            if exist "%%~D\condabin\conda.bat" set "CONDA_BAT=%%~D\condabin\conda.bat"
            if not defined CONDA_BAT if exist "%%~D\Scripts\conda.exe" set "CONDA_BAT=%%~D\Scripts\conda.exe"
            if not defined CONDA_BAT if exist "%%~D\Library\bin\conda.bat" set "CONDA_BAT=%%~D\Library\bin\conda.bat"
        )
    )
)

if defined CONDA_BAT goto :FOUND_CONDA

:NOT_FOUND
echo ============================================================================
echo [NOTICE] Miniforge / Conda was not detected on this computer.
echo ============================================================================
echo.

where winget >nul 2>nul
if not errorlevel 1 (
    echo Windows Package Manager (winget) is installed on your computer.
    echo Miniforge can be downloaded and installed automatically right now.
    echo.
    set "AUTO_WINGET=Y"
    set /p "AUTO_WINGET=Install Miniforge automatically now? [Y/N] (Default: Y): "
    if "!AUTO_WINGET!"=="" set "AUTO_WINGET=Y"
    if /i "!AUTO_WINGET!"=="Y" (
        echo.
        echo [INFO] Running winget install CondaForge.Miniforge3...
        echo        (This may take 1-3 minutes to download and configure).
        echo.
        winget install --id CondaForge.Miniforge3 -e --accept-source-agreements --accept-package-agreements
        echo.
        echo [INFO] Waiting 3 seconds for filesystem registration...
        timeout /t 3 >nul 2>nul
        goto :SCAN_FOR_CONDA
    )
)

echo.
echo Miniforge is free and open-source. To install manually:
echo.
echo   Option 1 (Command Prompt / PowerShell):
echo     Run:
echo       winget install --id CondaForge.Miniforge3 -e --accept-source-agreements --accept-package-agreements
echo.
echo   Option 2 (Direct Web Installer):
echo     Download from:
echo       https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe
echo     Run the installer, click 'Next', and accept default settings.
echo.
set "OPEN_BROWSER=N"
set /p "OPEN_BROWSER=Would you like to open the download page in your web browser now? [Y/N] (Default: N): "
if /i "!OPEN_BROWSER!"=="Y" (
    start https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe
)
echo.
echo After Miniforge finishes installing, double-click this file again!
echo.
pause
exit /b 1

:FOUND_CONDA
echo [INFO] Found Conda package manager at:
echo        %CONDA_BAT%
echo.
echo ============================================================================
echo [1/3] Creating / updating 'rcrc_platform' Python environment...
echo       (This downloads GIS, GDAL, and ML libraries via conda-forge.
echo        Depending on your internet speed, this may take 3-7 minutes).
echo ============================================================================
echo.

call "%CONDA_BAT%" env create -f "%~dp0environment.yml"
if errorlevel 1 (
    echo.
    echo [INFO] Environment may already exist or needs package update. Updating...
    call "%CONDA_BAT%" env update -f "%~dp0environment.yml" --prune
)

echo.
echo ============================================================================
echo [2/3] Locating and verifying 'rcrc_platform' environment...
echo ============================================================================

set "ENV_PYTHON="
set "ENV_ROOT="

:: Query Conda registry
for /f "tokens=1,2*" %%A in ('call "%CONDA_BAT%" env list 2^>nul') do (
    set "T_NAME=%%A"
    set "T_DIR=%%B"
    if "%%B"=="*" set "T_DIR=%%C"
    
    if /i "!T_NAME!"=="rcrc_platform" (
        if exist "!T_DIR!\python.exe" (
            set "ENV_ROOT=!T_DIR!"
            set "ENV_PYTHON=!T_DIR!\python.exe"
        )
    )
    if not defined ENV_PYTHON if /i "!T_NAME!"=="rcrc_3_1" (
        if exist "!T_DIR!\python.exe" (
            set "ENV_ROOT=!T_DIR!"
            set "ENV_PYTHON=!T_DIR!\python.exe"
        )
    )
    if not defined ENV_PYTHON if /i "!T_NAME!"=="rcrc" (
        if exist "!T_DIR!\python.exe" (
            set "ENV_ROOT=!T_DIR!"
            set "ENV_PYTHON=!T_DIR!\python.exe"
        )
    )
)

:: If not found through Conda registry, check standard file system locations
if not defined ENV_PYTHON (
    set "CANDIDATE_ENVS=%USERPROFILE%\.conda\envs\rcrc_platform;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform;%USERPROFILE%\miniforge3\envs\rcrc_platform;%LOCALAPPDATA%\Programs\miniforge3\envs\rcrc_platform;C:\miniforge3\envs\rcrc_platform;C:\ProgramData\miniforge3\envs\rcrc_platform;C:\Program Files\miniforge3\envs\rcrc_platform;%USERPROFILE%\.conda\envs\rcrc_3_1;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1;%USERPROFILE%\miniforge3\envs\rcrc_3_1;%USERPROFILE%\.conda\envs\rcrc;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc;%USERPROFILE%\miniforge3\envs\rcrc;%USERPROFILE%\miniconda3\envs\rcrc_platform;C:\ProgramData\miniconda3\envs\rcrc_platform"
    for %%P in ("!CANDIDATE_ENVS:;=" "!") do (
        if not defined ENV_PYTHON (
            if exist "%%~P\python.exe" (
                set "ENV_ROOT=%%~P"
                set "ENV_PYTHON=%%~P\python.exe"
            )
        )
    )
)

if not defined ENV_PYTHON (
    echo.
    echo [ERROR] The environment 'rcrc_platform' was not found after creation.
    echo Please review the Conda output above for errors.
    echo.
    pause
    exit /b 1
)

echo [INFO] Detected Python executable:
echo        !ENV_PYTHON!
echo.
echo ============================================================================
echo [3/3] Testing core GIS and ML packages (Streamlit, GDAL, GeoPandas, GEE)...
echo ============================================================================

"!ENV_PYTHON!" -c "import streamlit, rasterio, geopandas, sklearn, ee, PIL; print('[OK] All core packages imported successfully!')"
if errorlevel 1 (
    echo.
    echo [ERROR] Environment was created, but one or more required packages failed to load.
    echo Please review the error message above.
    echo.
    pause
    exit /b 1
)

:: Save resolved Python path to .conda_env_path for instant launcher discovery
> "%~dp0.conda_env_path" echo !ENV_PYTHON!

echo.
echo ============================================================================
echo [SUCCESS] Environment setup and verification complete!
echo ============================================================================
echo.
echo Python Environment: !ENV_PYTHON!
echo Configuration saved: %~dp0.conda_env_path
echo.
echo Next steps:
echo   1. Double-click 'launch_platform.bat' to start the RCR platform.
echo   2. (Optional) If you plan to use Sentinel-2 satellite data in Step 2,
echo      double-click 'authenticate_earth_engine.bat' to authorize Google Earth Engine.
echo.
pause
