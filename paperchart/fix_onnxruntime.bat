@echo off
echo ============================================================
echo  Fix onnxruntime DLL initialization error
echo ============================================================
echo.
echo このバッチは Anaconda/Miniconda 環境で
echo "ImportError: DLL load failed while importing onnxruntime_pybind11_state"
echo を解消します。原因は conda の vs2015_runtime パッケージが古いためです。
echo.
echo Microsoft Store 版 / python.org 版 Python を使っている場合は、
echo 最新の VC++ Redistributable をインストールしてください。
echo.
pause

where conda >nul 2>&1
if %errorlevel% neq 0 (
  echo [ERROR] conda コマンドが見つかりません。
  echo Anaconda Prompt から実行するか、conda を PATH に追加してください。
  pause
  exit /b 1
)

echo.
echo conda update vs2015_runtime ...
conda update vs2015_runtime -y -n base
if %errorlevel% neq 0 (
  echo 標準チャネルで失敗したため、conda-forge から再試行します ...
  conda install conda-forge::vs2015_runtime -y -n base
)

echo.
echo 完了。paperChart と Anaconda Prompt をすべて閉じ、再度実行してください。
pause
