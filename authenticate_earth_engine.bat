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

set "ENV_PYTHON="
set "ENV_ROOT="

:: 1. Bundled portable environment
if exist "%~dp0env\python.exe" (
    set "ENV_PYTHON=%~dp0env\python.exe"
    goto :RUN_AUTH
)

:: 2. Standard local Miniforge rcrc_platform or rcrc environments
if exist "%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform\python.exe" (
    set "ENV_PYTHON=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_platform\python.exe"
    goto :RUN_AUTH
)
if exist "%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1\python.exe" (
    set "ENV_PYTHON=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc_3_1\python.exe"
    goto :RUN_AUTH
)
if exist "%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc\python.exe" (
    set "ENV_PYTHON=%USERPROFILE%\AppData\Local\miniforge3\envs\rcrc\python.exe"
    goto :RUN_AUTH
)

:: 3. Alternative Conda paths
set "CANDIDATE_PATHS=%USERPROFILE%\miniforge3\envs\rcrc_platform;%USERPROFILE%\miniforge3\envs\rcrc_3_1;%USERPROFILE%\miniforge3\envs\rcrc;%USERPROFILE%\miniconda3\envs\rcrc_platform;%USERPROFILE%\miniconda3\envs\rcrc_3_1;%USERPROFILE%\miniconda3\envs\rcrc;C:\miniforge3\envs\rcrc_platform;C:\miniforge3\envs\rcrc_3_1;C:\miniforge3\envs\rcrc;C:\ProgramData\miniforge3\envs\rcrc_platform;C:\ProgramData\miniforge3\envs\rcrc_3_1"
for %%P in ("%CANDIDATE_PATHS:;=" "%") do (
    if exist "%%~P\python.exe" (
        set "ENV_PYTHON=%%~P\python.exe"
        goto :RUN_AUTH
    )
)

:: 4. System Python
where python >nul 2>nul
if not errorlevel 1 (
    for /f "tokens=*" %%I in ('where python') do (
        set "ENV_PYTHON=%%I"
        goto :RUN_AUTH
    )
)

:NOT_FOUND
echo ============================================================================
echo [ERROR] No Python environment was found.
echo Please run 'setup_environment.bat' first to build the platform environment.
echo ============================================================================
pause
exit /b 1

:RUN_AUTH
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
