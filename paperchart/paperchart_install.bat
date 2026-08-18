@echo off
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo  B650Video paperChart module installer
echo ============================================================
echo.

set /p PCDIR="Enter full path of your paperChart folder (e.g. C:\paperChart): "

if not exist "%PCDIR%\BIN" (
  echo [ERROR] BIN folder not found in %PCDIR%
  pause
  exit /b 1
)

set "PYCMD=python"
py -3 --version >nul 2>&1 && set "PYCMD=py -3"

set "SRC=%~dp0"
set "AR_DIR=%PCDIR%\anesthesia-record"
set "PCMON=%PCDIR%\BIN\monitors"
set "PCCONF=%PCDIR%\CONF\monitors"
set "DIRCNF=%PCDIR%\CONF\dircnf.txt"

if not exist "%PCMON%" mkdir "%PCMON%"
if not exist "%PCCONF%" mkdir "%PCCONF%"

echo.
echo Copying module files...
copy /Y "%SRC%BIN\monitors\B650Video.exe" "%PCMON%\"
copy /Y "%SRC%BIN\monitors\PpcCtrl.dll" "%PCMON%\"
copy /Y "%SRC%CONF\monitors\B650Video.txt" "%PCCONF%\"

echo.
echo Copying Python package to %AR_DIR% ...
if exist "%AR_DIR%" rmdir /S /Q "%AR_DIR%"
robocopy "%SRC%.." "%AR_DIR%" /E /XD .git .venv .pytest_cache __pycache__ /XF *.pyc /R:1 /W:1 >nul 2>&1

echo.
echo Checking Anaconda vs2015_runtime (needed for onnxruntime DLL)...
where conda >nul 2>&1
if %errorlevel%==0 (
  echo   Updating vs2015_runtime via conda...
  call conda install conda-forge::vs2015_runtime -y -n base >nul 2>&1
) else (
  echo   conda not found; please run fix_onnxruntime.bat from Anaconda Prompt if needed.
)

echo.
echo Creating Python virtual environment and installing dependencies...
%PYCMD% -m venv "%AR_DIR%\.venv"
"%AR_DIR%\.venv\Scripts\python.exe" -m pip install --upgrade pip
if exist "%AR_DIR%\requirements.txt" (
  "%AR_DIR%\.venv\Scripts\python.exe" -m pip install -r "%AR_DIR%\requirements.txt"
) else (
  echo [WARNING] requirements.txt not found in %AR_DIR%
)

echo.
echo Updating B650Video.txt to use the venv Python...
set "PY_PATH=%PCDIR%\anesthesia-record\.venv\Scripts\python.exe"
powershell -NoProfile -Command "(Get-Content '%PCCONF%\B650Video.txt' -Encoding Default) -replace '^@Python=.*', '@Python=%PY_PATH%' | Set-Content '%PCCONF%\B650Video.txt' -Encoding Default"

echo.
echo Updating %DIRCNF% ...
if not exist "%DIRCNF%" (
  %PYCMD% "%SRC%patch_dircnf.py" "%DIRCNF%"
) else (
  copy /Y "%DIRCNF%" "%DIRCNF%.bak" >nul
  %PYCMD% "%SRC%patch_dircnf.py" "%DIRCNF%"
)

echo.
echo ---------------------------------------------------------------
echo Installation complete.
echo.
echo %DIRCNF% updated. Backup: %DIRCNF%.bak
echo.
echo Next: edit %PCCONF%\B650Video.txt and set @Device to your UVC index.
echo   @Device=0
echo ---------------------------------------------------------------
echo.
pause
