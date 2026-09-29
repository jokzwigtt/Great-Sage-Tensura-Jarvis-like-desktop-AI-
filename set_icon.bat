@echo off
title Set Great Sage icon
cd /d "%~dp0"
python -m pip install -q pillow
python make_icon.py || (pause & exit /b)

for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
del "%USERPROFILE%\Desktop\Great Sage.lnk" 2>nul
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Great Sage.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0assistant.py\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0sage.ico'; $s.Description='Great Sage AI assistant'; $s.Save()"
ie4uinit.exe -show >nul 2>&1

echo.
echo Done! The desktop shortcut and the app window now use your picture.
echo (If the old icon still shows, right-click the desktop and hit Refresh.)
pause
