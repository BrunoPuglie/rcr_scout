@echo off
setlocal enabledelayedexpansion
title Soybean RCR Field Scouting Platform
cd /d "%~dp0"

echo ============================================================================
echo   Soybean Red Crown Rot (RCR) - Automated Field Scouting Platform
echo   Smart Plant Pathology Lab - University of Illinois Urbana-Champaign
echo ============================================================================
echo.

:RESOLVE_ENV
set "ENV_ROOT="
set "ENV_PYTHON="

:: 1. Priority 1: Check saved environment config file (.conda_env_path)
:CHECK_SAVED_ENV
if exist "%~dp0.conda_env_path" (
    set /p SAVED_PYTHON=<"%~dp0.conda_env_path"
    if exist "!SAVED_PYTHON!" (
        set "ENV_PYTHON=!SAVED_PYTHON!"
        for %%D in ("!SAVED_PYTHON!\..") do set "ENV_ROOT=%%~fD"
        echo [INFO] Using configured Python environment from .conda_env_path
        goto :FOUND_ENV
    )
)

:: 2. Priority 2: Bundled portable standalone environment in .\env\
if exist "%~dp0env\python.exe" (
    set "ENV_ROOT=%~dp0env"
    set "ENV_PYTHON=%~dp0env\python.exe"
    echo [INFO] Using bundled portable Python environment in .\env\
    goto :FOUND_ENV
)

:: 3. Priority 3: Query Conda dynamically if installed
set "CONDA_BAT="
where conda >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('where conda') do (
        if not defined CONDA_BAT set "CONDA_BAT=%%I"
    )
)

set "CANDIDATE_CONDA_DIRS=%USERPROFILE%\AppData\Local\miniforge3;%USERPROFILE%\miniforge3;%LOCALAPPDATA%\miniforge3;%LOCALAPPDATA%\Programs\miniforge3;%USERPROFILE%\AppData\Local\Programs\miniforge3;C:\miniforge3;C:\ProgramData\miniforge3;C:\Program Files\miniforge3;%USERPROFILE%\miniconda3;%USERPROFILE%\anaconda3;C:\miniconda3;C:\ProgramData\miniconda3;C:\Program Files\miniconda3;C:\anaconda3;C:\ProgramData\anaconda3;C:\Program Files\anaconda3"
if not defined CONDA_BAT (
    for %%D in ("%CANDIDATE_CONDA_DIRS:;=" "%") do (
        if not defined CONDA_BAT (
            if exist "%%~D\condabin\conda.bat" set "CONDA_BAT=%%~D\condabin\conda.bat"
            if not defined CONDA_BAT if exist "%%~D\Scripts\conda.exe" set "CONDA_BAT=%%~D\Scripts\conda.exe"
            if not defined CONDA_BAT if exist "%%~D\Library\bin\conda.bat" set "CONDA_BAT=%%~D\Library\bin\conda.bat"
        )
    )
)

if defined CONDA_BAT (
    for /f "tokens=1,2*" %%A in ('call "%CONDA_BAT%" env list 2^>nul') do (
        set "T_NAME=%%A"
        set "T_DIR=%%B"
        if "%%B"=="*" set "T_DIR=%%C"
        
        if /i "!T_NAME!"=="rcrc_platform" (
            if exist "!T_DIR!\python.exe" (
                set "ENV_ROOT=!T_DIR!"
                set "ENV_PYTHON=!T_DIR!\python.exe"
                echo [INFO] Found '!T_NAME!' via Conda registry: !ENV_ROOT!
                goto :FOUND_ENV
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
    if defined ENV_PYTHON (
        echo [INFO] Found Conda environment via Conda registry: !ENV_ROOT!
        goto :FOUND_ENV
    )
)

:: 4. Priority 4: Search standard Conda / Miniforge file system paths
set "CANDIDATE_PATHS=%USERPROFILE%\.conda\envs\rcrc_platform;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform;%USERPROFILE%\miniforge3\envs\rcrc_platform;%LOCALAPPDATA%\Programs\miniforge3\envs\rcrc_platform;C:\miniforge3\envs\rcrc_platform;C:\ProgramData\miniforge3\envs\rcrc_platform;C:\Program Files\miniforge3\envs\rcrc_platform;%USERPROFILE%\.conda\envs\rcrc_3_1;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1;%USERPROFILE%\miniforge3\envs\rcrc_3_1;%USERPROFILE%\.conda\envs\rcrc;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc;%USERPROFILE%\miniforge3\envs\rcrc;%USERPROFILE%\miniconda3\envs\rcrc_platform;%USERPROFILE%\miniconda3\envs\rcrc_3_1;%USERPROFILE%\anaconda3\envs\rcrc_platform;C:\ProgramData\miniconda3\envs\rcrc_platform;C:\Program Files\miniconda3\envs\rcrc_platform"
for %%P in ("!CANDIDATE_PATHS:;=" "!") do (
    if not defined ENV_PYTHON (
        if exist "%%~P\python.exe" (
            set "ENV_ROOT=%%~P"
            set "ENV_PYTHON=%%~P\python.exe"
            echo [INFO] Found Conda environment at: %%~P
            goto :FOUND_ENV
        )
    )
)

:: 5. Priority 5: Check if system Python has required packages
python -c "import streamlit, rasterio, geopandas, sklearn" 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('where python') do (
        if not defined ENV_PYTHON (
            set "ENV_PYTHON=%%I"
            for %%D in ("%%I\..") do set "ENV_ROOT=%%~fD"
            echo [INFO] Using system Python from PATH: !ENV_PYTHON!
            goto :FOUND_ENV
        )
    )
)

:NOT_FOUND
echo ============================================================================
echo [NOTICE] RCR platform environment was not found on this computer.
echo ============================================================================
echo.
echo A compatible Python environment with geospatial packages is required.
echo.
set "RUN_SETUP=Y"
set /p "RUN_SETUP=Would you like to run the automated environment setup now? [Y/N] (Default: Y): "
if "!RUN_SETUP!"=="" set "RUN_SETUP=Y"
if /i "!RUN_SETUP!"=="Y" (
    echo.
    echo [INFO] Launching setup_environment.bat...
    call "%~dp0setup_environment.bat"
    if exist "%~dp0.conda_env_path" (
        goto :CHECK_SAVED_ENV
    )
    goto :RESOLVE_ENV
)

echo.
echo If you prefer to set up manually:
echo   1. If Miniforge is not installed, run in PowerShell:
echo        winget install --id CondaForge.Miniforge3 -e --accept-source-agreements --accept-package-agreements
echo      (Or download from: https://github.com/conda-forge/miniforge/releases)
echo.
echo   2. Double-click 'setup_environment.bat' in this folder.
echo.
echo   3. Double-click 'launch_platform.bat' again to launch!
echo.
pause
exit /b 1

:FOUND_ENV
if not defined ENV_PYTHON if defined ENV_ROOT set "ENV_PYTHON=%ENV_ROOT%\python.exe"
if not defined ENV_ROOT if defined ENV_PYTHON for %%D in ("%ENV_PYTHON%\..") do set "ENV_ROOT=%%~fD"

:: Cache path for immediate discovery on next launch
> "%~dp0.conda_env_path" echo !ENV_PYTHON!

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
