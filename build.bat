@echo off
echo ============================================
echo  Building fix_rotation_metadata.exe (Nuitka)
echo ============================================
echo.
echo Build started: %DATE% %TIME%
echo.

:: Show Python version
echo [1/4] Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.8+ and add it to PATH.
    pause
    exit /b 1
)
echo.

:: Show pip version
echo [2/4] Checking pip...
pip --version
echo.

:: Check and show Nuitka version (also prints detected C compiler)
echo [3/4] Checking Nuitka...
python -m nuitka --version
if errorlevel 1 (
    echo Nuitka not found. Installing...
    pip install nuitka
    if errorlevel 1 (
        echo ERROR: Failed to install Nuitka.
        pause
        exit /b 1
    )
    echo.
    echo Nuitka installed. Version info:
    python -m nuitka --version
)
echo.

:: Check and show h5py version
echo [4/4] Checking h5py...
python -c "import h5py; print('h5py version:', h5py.version.version); print('HDF5 version:', h5py.version.hdf5_version)"
if errorlevel 1 (
    echo h5py not found. Installing...
    pip install h5py
    if errorlevel 1 (
        echo ERROR: Failed to install h5py.
        pause
        exit /b 1
    )
    echo.
    python -c "import h5py; print('h5py version:', h5py.version.version); print('HDF5 version:', h5py.version.hdf5_version)"
)
echo.

echo ============================================
echo  All prerequisites OK. Starting compilation.
echo ============================================
echo.
echo Nuitka compilation started: %DATE% %TIME%
echo This may take several minutes. Verbose output follows:
echo.

python -m nuitka ^
    --onefile ^
    --standalone ^
    --output-filename=fix_rotation_metadata.exe ^
    --assume-yes-for-downloads ^
    --verbose ^
    --show-progress ^
    --show-memory ^
    --show-modules ^
    fix_rotation_metadata.py

if errorlevel 1 (
    echo.
    echo ============================================
    echo  ERROR: Build failed at %DATE% %TIME%
    echo ============================================
    echo.
    echo Common issues:
    echo   - No C compiler found: install Visual Studio Build Tools or MinGW64
    echo   - Missing module: check the --show-modules output above
    echo   - Out of memory: close other applications and retry
    echo   - Antivirus blocking: add this directory to exclusions
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build complete: fix_rotation_metadata.exe
echo  Finished at: %DATE% %TIME%
echo ============================================
pause
