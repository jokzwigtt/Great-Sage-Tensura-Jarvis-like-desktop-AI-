@echo off
title Great Sage - cloned voice setup (F5-TTS)
cd /d "%~dp0"
python --version
echo === 1/3 Installing PyTorch for your NVIDIA graphics card ===
echo This is a big download (a few GB). It only needs to run once.
python -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
  echo.
  echo PyTorch could not be installed. Copy the red error text above and send it to Claude.
  pause & exit /b
)

echo === 2/3 Installing the voice cloning engine (F5-TTS) ===
rem torch is pinned here too so F5-TTS cannot swap in a different (non-NVIDIA) version
python -m pip install f5-tts torch==2.9.1 torchaudio==2.9.1 --extra-index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
  echo.
  echo F5-TTS could not be installed. Copy the red error text above and send it to Claude.
  pause & exit /b
)
rem make sure installing F5-TTS did not swap in a non-NVIDIA PyTorch
python -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" || python -m pip install --force-reinstall --no-deps torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128

echo === 3/3 Checking ===
python -c "import torch;print('Graphics card ready:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '(NOT found - voice will be very slow)')"
python -c "from f5_tts.api import F5TTS;print('F5-TTS installed OK')"
echo.
echo Done. Restart the Great Sage. The first start downloads the voice model (about 1.5 GB).
pause
