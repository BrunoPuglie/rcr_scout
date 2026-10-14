@echo off
setlocal enabledelayedexpansion
title Soybean RCR Field Scouting Platform
cd /d "%~dp0"

echo ============================================================================
echo   Soybean Red Crown Rot (RCR) - Automated Field Scouting Platform
echo   Smart Plant Pathology Lab - University of Illinois Urbana-Champaign
echo ============================================================================
echo.

set "ENV_ROOT="
set "ENV_PYTHON="

:: 1. Priority 1: Portable / bundled standalone environment in rcr_scouting_app\env\
if exist "%~dp0env\python.exe" (
    set "ENV_ROOT=%~dp0env"
    set "ENV_PYTHON=%~dp0env\python.exe"
    echo [INFO] Using bundled portable Python environment in .\env\
    goto :FOUND_ENV
)

:: 2. Priority 2: Standard local Miniforge rcrc, rcrc_platform or rcrc_3_1 environment
if exist "%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc\python.exe" (
    set "ENV_ROOT=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc"
    set "ENV_PYTHON=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc\python.exe"
    echo [INFO] Found local Miniforge rcrc environment.
    goto :FOUND_ENV
)
if exist "%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform\python.exe" (
    set "ENV_ROOT=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform"
    set "ENV_PYTHON=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform\python.exe"
    echo [INFO] Found local Miniforge rcrc_platform environment.
    goto :FOUND_ENV
)
if exist "%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1\python.exe" (
    set "ENV_ROOT=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1"
    set "ENV_PYTHON=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1\python.exe"
    echo [INFO] Found local Miniforge rcrc_3_1 environment.
    goto :FOUND_ENV
)

:: 3. Priority 3: Alternative Conda / Miniconda / Anaconda paths
set "CANDIDATE_PATHS=%USERPROFILE%\miniforge3\envs\rcrc;%USERPROFILE%\miniforge3\envs\rcrc_platform;%USERPROFILE%\miniforge3\envs\rcrc_3_1;%USERPROFILE%\miniconda3\envs\rcrc;%USERPROFILE%\miniconda3\envs\rcrc_platform;%USERPROFILE%\miniconda3\envs\rcrc_3_1;%USERPROFILE%\anaconda3\envs\rcrc;%USERPROFILE%\anaconda3\envs\rcrc_platform;%USERPROFILE%\anaconda3\envs\rcrc_3_1;C:\miniforge3\envs\rcrc;C:\miniforge3\envs\rcrc_platform;C:\miniforge3\envs\rcrc_3_1;C:\ProgramData\miniforge3\envs\rcrc;C:\ProgramData\miniforge3\envs\rcrc_platform;C:\ProgramData\miniforge3\envs\rcrc_3_1"
for %%P in ("%CANDIDATE_PATHS:;=" "%") do (
    if exist "%%~P\python.exe" (
        set "ENV_ROOT=%%~P"
        set "ENV_PYTHON=%%~P\python.exe"
        echo [INFO] Found Conda environment at: %%~P
        goto :FOUND_ENV
    )
)

:: 4. Priority 4: Check if system Python has required packages
python -c "import streamlit, rasterio, geopandas, sklearn" 2>nul
if not errorlevel 1 (
    for /f "tokens=*" %%I in ('where python') do (
        set "ENV_PYTHON=%%I"
        for %%D in ("%%I\..") do set "ENV_ROOT=%%~fD"
        echo [INFO] Using system Python from PATH: !ENV_PYTHON!
        goto :FOUND_ENV
    )
)

:NOT_FOUND
echo ============================================================================
echo [ERROR] No compatible Python environment with geospatial packages was found.
echo ============================================================================
echo.
echo To set up this platform in 1 click:
echo.
echo   1. If you do not have Miniforge yet, install it by opening PowerShell and running:
echo        winget install conda-forge.miniforge
echo      (Or download from https://github.com/conda-forge/miniforge/releases)
echo.
echo   2. Double-click 'setup_environment.bat' in this folder.
echo      (It will automatically configure the required environment).
echo.
echo   3. Double-click 'launch_platform.bat' again to launch!
echo.
echo Press any key to exit...
pause >nul
exit /b 1

:FOUND_ENV
if not defined ENV_PYTHON if defined ENV_ROOT set "ENV_PYTHON=%ENV_ROOT%\python.exe"
if not defined ENV_ROOT if defined ENV_PYTHON for %%D in ("%ENV_PYTHON%\..") do set "ENV_ROOT=%%~fD"
:: Configure PATH and GDAL/PROJ DLL directories
set "PATH=%ENV_ROOT%;%ENV_ROOT%\Library\mingw-w64\bin;%ENV_ROOT%\Library\usr\bin;%ENV_ROOT%\Library\bin;%ENV_ROOT%\Scripts;%ENV_ROOT%\bin;%PATH%"
if exist "%ENV_ROOT%\Library\share\gdal" set "GDAL_DATA=%ENV_ROOT%\Library\share\gdal"
if exist "%ENV_ROOT%\Library\share\proj" set "PROJ_LIB=%ENV_ROOT%\Library\share\proj"
set "SHAPE_RESTORE_SHX=YES"
set "MPLBACKEND=Agg"
set "KMP_DUPLICATE_LIB_OK=TRUE"

echo.
echo [1/2] Verifying Streamlit web engine...
"%ENV_PYTHON%" -c "import streamlit" 2>nul
if errorlevel 1 (
    echo       Installing Streamlit into environment...
    "%ENV_PYTHON%" -m pip install streamlit
)

echo.
echo [2/2] Launching RCR Scouting Web Platform in your default browser...
echo       (Keep this command window open while using the platform. Close it or press Ctrl+C to stop.)
echo.

"%ENV_PYTHON%" -m streamlit run "app\app.py"
pause
