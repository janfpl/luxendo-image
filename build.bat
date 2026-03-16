@echo off
echo ============================================
echo  Building fix_rotation_metadata.exe (Nuitka)
echo ============================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.8+ and add it to PATH.
    pause
    exit /b 1
)

:: Check Nuitka is installed
python -m nuitka --version >nul 2>&1
if errorlevel 1 (
    echo Nuitka not found. Installing...
    pip install nuitka
    if errorlevel 1 (
        echo ERROR: Failed to install Nuitka.
        pause
        exit /b 1
    )
)

:: Check h5py is installed
python -c "import h5py" >nul 2>&1
if errorlevel 1 (
    echo h5py not found. Installing...
    pip install h5py
    if errorlevel 1 (
        echo ERROR: Failed to install h5py.
        pause
        exit /b 1
    )
)

echo.
echo Starting Nuitka compilation (this may take a few minutes)...
echo.

python -m nuitka ^
    --onefile ^
    --standalone ^
    --output-filename=fix_rotation_metadata.exe ^
    --assume-yes-for-downloads ^
    fix_rotation_metadata.py

if errorlevel 1 (
    echo.
    echo ERROR: Build failed.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build complete: fix_rotation_metadata.exe
echo ============================================
pause
