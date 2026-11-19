@echo off
setlocal
rem ---------------------------------------------------------------
rem WirelessNetOptimizer optional installer
rem Copies the single-file exe to LocalAppData and creates shortcuts.
rem This is optional: you can also just double-click dist\*.exe directly.
rem ---------------------------------------------------------------
set "APPNAME=WirelessNetOptimizer"
set "SRC=%~dp0dist\%APPNAME%.exe"
set "DST=%LOCALAPPDATA%\%APPNAME%"

if not exist "%SRC%" (
  echo [ERROR] Cannot find "%SRC%"
  echo Please keep this .bat next to the "dist" folder.
  pause
  exit /b 1
)

echo Installing %APPNAME% ...
if not exist "%DST%" mkdir "%DST%"
copy /Y "%SRC%" "%DST%\%APPNAME%.exe" >nul
if errorlevel 1 (
  echo [ERROR] Copy failed. Close the running program and try again.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); $p=[Environment]::GetFolderPath('Programs'); $exe='%DST%\%APPNAME%.exe'; $s=$w.CreateShortcut([IO.Path]::Combine($d,'%APPNAME%.lnk')); $s.TargetPath=$exe; $s.WorkingDirectory='%DST%'; $s.IconLocation=$exe; $s.Description='Wireless Net Optimizer'; $s.Save(); $m=$w.CreateShortcut([IO.Path]::Combine($p,'%APPNAME%.lnk')); $m.TargetPath=$exe; $m.WorkingDirectory='%DST%'; $m.IconLocation=$exe; $m.Description='Wireless Net Optimizer'; $m.Save()"

echo.
echo [OK] Installed to: %DST%
echo [OK] Desktop + Start Menu shortcuts created.
echo.
echo Uninstall: delete the folder above, both .lnk shortcuts, and
echo            %%APPDATA%%\%APPNAME% (your saved projects / floorplans).
echo.
echo Double-click the desktop shortcut to start. A console window shows
echo the local URL; close that window to stop the service.
echo.
pause
