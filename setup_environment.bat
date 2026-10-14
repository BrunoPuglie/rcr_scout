@echo off
setlocal enabledelayedexpansion
title Setup RCR Platform Environment
cd /d "%~dp0"

echo ============================================================================
echo   Soybean Red Crown Rot (RCR) Platform - One-Time Environment Setup
echo   Smart Plant Pathology Lab - University of Illinois Urbana-Champaign
echo ============================================================================
echo.

set "CONDA_BAT="

:: Check if conda is in PATH
where conda >nul 2>nul
if not errorlevel 1 (
    for /f "tokens=*" %%I in ('where conda') do (
        set "CONDA_BAT=%%I"
        goto :FOUND_CONDA
    )
)

:: Search known default Miniforge / Miniconda locations
set "CANDIDATE_DIRS=%USERPROFILE%\AppData\Local\miniforge3;%USERPROFILE%\miniforge3;%USERPROFILE%\miniconda3;%USERPROFILE%\anaconda3;C:\miniforge3;C:\ProgramData\miniforge3"
for %%D in ("%CANDIDATE_DIRS:;=" "%") do (
    if exist "%%~D\condabin\conda.bat" (
        set "CONDA_BAT=%%~D\condabin\conda.bat"
        goto :FOUND_CONDA
    )
    if exist "%%~D\Scripts\conda.exe" (
        set "CONDA_BAT=%%~D\Scripts\conda.exe"
        goto :FOUND_CONDA
    )
)

:NOT_FOUND
echo ============================================================================
echo [NOTICE] Miniforge / Conda was not found on your computer.
echo ============================================================================
echo.
echo Installing Miniforge takes about 1-2 minutes:
echo.
echo   Option 1 (Fastest via Windows Terminal / PowerShell):
echo     Run this single command:
echo       winget install conda-forge.miniforge
echo.
echo   Option 2 (Web Installer):
echo     Download the Windows installer from:
echo       https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe
echo     Run the installer, click 'Next' and accept the defaults.
echo.
echo After Miniforge finishes installing, double-click this file again!
echo.
pause
exit /b 1

:FOUND_CONDA
echo [INFO] Found Conda package manager at:
echo        %CONDA_BAT%
echo.
echo [1/2] Creating 'rcrc_platform' Python environment with all GIS and ML tools...
echo       (This may take 3-5 minutes depending on your internet connection).
echo.

call "%CONDA_BAT%" env create -f "%~dp0environment.yml"
if errorlevel 1 (
    echo.
    echo [WARNING] Environment creation reported an issue. Trying to update if already partially created...
    call "%CONDA_BAT%" env update -f "%~dp0environment.yml" --prune
)

echo.
echo ============================================================================
echo [SUCCESS] Environment setup complete!
echo ============================================================================
echo.
echo You can now double-click 'launch_platform.bat' to start the RCR platform.
echo.
pause
