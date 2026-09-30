@echo off
title Great Sage setup
cd /d "%~dp0"

echo === 1/3 Checking Python ===
python --version >nul 2>&1
if errorlevel 1 (
  echo Python not found. Install it from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add python.exe to PATH" in the installer, then run this again.
  pause & exit /b
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo Installing the natural AI voice (Kokoro)...
python -m pip install kokoro-onnx --ignore-requires-python || echo The AI voice could not be installed - the Windows voice will be used instead.
if exist "voice\great_sage.wav" (
  echo Installing the Great Sage cloned voice - needs an NVIDIA graphics card...
  call install_cloned_voice.bat
)

echo === 2/3 Checking Ollama (local AI brain) ===
where ollama >nul 2>&1
if errorlevel 1 (
  echo Ollama not found - installing it with winget...
  winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
  echo If that failed, download it from https://ollama.com/download then run this again.
  set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"
)
echo Downloading the AI model (about 8 GB, one time only)...
ollama pull gemma4:12b

echo === 3/3 Creating desktop and Start menu shortcuts ===
for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
powershell -NoProfile -Command "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { $s=(New-Object -ComObject WScript.Shell).CreateShortcut($d+'\Great Sage.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0assistant.py\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0sage.ico'; $s.Description='Great Sage AI assistant'; $s.Save() }"
echo.
echo Done! Double-click "Great Sage" on your desktop to start.
echo To pin it: Start menu, find Great Sage, right-click, Pin to taskbar.
pause
