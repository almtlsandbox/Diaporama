@echo off
setlocal
rem Glissez un dossier sur ce fichier, ou double-cliquez et tapez le chemin.
set "PY=python"
where py >nul 2>nul && set "PY=py"
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython
%PY% "%~dp0slideshow.py" %*
if errorlevel 1 pause
exit /b
:nopython
echo Python n est pas installe sur ce PC.
echo Installez "Python 3" depuis le Microsoft Store, puis relancez ce fichier.
pause
