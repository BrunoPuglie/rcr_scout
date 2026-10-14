@echo off
setlocal enabledelayedexpansion
title Google Earth Engine Authentication - RCR Platform
cd /d "%~dp0"

echo ============================================================================
echo   Soybean Red Crown Rot (RCR) Platform - Earth Engine Authentication
echo   Smart Plant Pathology Lab - University of Illinois Urbana-Champaign
echo ============================================================================
echo.
echo This tool authorizes Google Earth Engine on your computer (one-time setup).
echo A web browser will open asking you to sign in with your Google account.
echo.
echo Prerequisites:
echo   1. Have a Google account registered for Earth Engine:
echo      If not registered yet, sign up for free at:
echo      https://code.earthengine.google.com
echo   2. Note your Project ID (e.g. ee-yourusername or your-gcp-project-id).
echo.
echo Press any key to begin Google authorization...
pause >nul

:RESOLVE_ENV
set "ENV_PYTHON="
set "ENV_ROOT="

:: 1. Priority 1: Check saved environment config file (.conda_env_path)
:CHECK_SAVED_ENV
if exist "%~dp0.conda_env_path" (
    set /p SAVED_PYTHON=<"%~dp0.conda_env_path"
    if exist "!SAVED_PYTHON!" (
        set "ENV_PYTHON=!SAVED_PYTHON!"
        for %%D in ("!SAVED_PYTHON!\..") do set "ENV_ROOT=%%~fD"
        goto :RUN_AUTH
    )
)

:: 2. Priority 2: Bundled portable standalone environment in .\env\
if exist "%~dp0env\python.exe" (
    set "ENV_PYTHON=%~dp0env\python.exe"
    set "ENV_ROOT=%~dp0env"
    goto :RUN_AUTH
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
                goto :RUN_AUTH
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
    if defined ENV_PYTHON goto :RUN_AUTH
)

:: 4. Priority 4: Search standard Conda / Miniforge file system paths
set "CANDIDATE_PATHS=%USERPROFILE%\.conda\envs\rcrc_platform;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform;%USERPROFILE%\miniforge3\envs\rcrc_platform;%LOCALAPPDATA%\Programs\miniforge3\envs\rcrc_platform;C:\miniforge3\envs\rcrc_platform;C:\ProgramData\miniforge3\envs\rcrc_platform;C:\Program Files\miniforge3\envs\rcrc_platform;%USERPROFILE%\.conda\envs\rcrc_3_1;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1;%USERPROFILE%\miniforge3\envs\rcrc_3_1;%USERPROFILE%\.conda\envs\rcrc;%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc;%USERPROFILE%\miniforge3\envs\rcrc;%USERPROFILE%\miniconda3\envs\rcrc_platform;%USERPROFILE%\miniconda3\envs\rcrc_3_1;%USERPROFILE%\anaconda3\envs\rcrc_platform;C:\ProgramData\miniconda3\envs\rcrc_platform;C:\Program Files\miniconda3\envs\rcrc_platform"
for %%P in ("!CANDIDATE_PATHS:;=" "!") do (
    if not defined ENV_PYTHON (
        if exist "%%~P\python.exe" (
            set "ENV_ROOT=%%~P"
            set "ENV_PYTHON=%%~P\python.exe"
            goto :RUN_AUTH
        )
    )
)

:: 5. Priority 5: System Python
where python >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('where python') do (
        if not defined ENV_PYTHON (
            set "ENV_PYTHON=%%I"
            goto :RUN_AUTH
        )
    )
)

:NOT_FOUND
echo ============================================================================
echo [NOTICE] RCR platform Python environment was not found.
echo ============================================================================
echo.
set "RUN_SETUP=Y"
set /p "RUN_SETUP=Would you like to run setup_environment.bat now? [Y/N] (Default: Y): "
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
echo Please run 'setup_environment.bat' first to build the platform environment.
echo ============================================================================
pause
exit /b 1

:RUN_AUTH
if not defined ENV_ROOT if defined ENV_PYTHON for %%D in ("%ENV_PYTHON%\..") do set "ENV_ROOT=%%~fD"

:: Cache path for quick discovery
> "%~dp0.conda_env_path" echo !ENV_PYTHON!

echo.
echo [INFO] Running Earth Engine authorization with:
echo        %ENV_PYTHON%
echo.
"%ENV_PYTHON%" -c "import ee; ee.Authenticate()"
if errorlevel 1 (
    echo.
    echo ============================================================================
    echo [NOTICE] Earth Engine authorization was not completed or was cancelled.
    echo You can re-run this file anytime to try again.
    echo ============================================================================
) else (
    echo.
    echo ============================================================================
    echo [SUCCESS] Google Earth Engine is now authenticated on this computer!
    echo ============================================================================
    echo.
    echo Next steps:
    echo   1. Double-click 'launch_platform.bat' to start the RCR scouting app.
    echo   2. In the left sidebar under 'Google Earth Engine project ID', enter your
    echo      Project ID (e.g. ee-yourusername).
    echo   3. Run Step 2 (Sentinel-2 NDVI Season Progress) to get scouting dates!
    echo.
)
pause
