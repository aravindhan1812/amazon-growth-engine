@echo off
setlocal
cd /d "%~dp0"

echo ==================================================
echo   Pushing amazon-growth-engine to GitHub
echo ==================================================
echo Folder: %cd%
echo.

git --version >nul 2>&1
if errorlevel 1 (
    echo [X] Git is not installed.
    echo     Download it from https://git-scm.com/downloads
    echo     Install with the default options, then run this file again.
    echo.
    pause
    exit /b 1
)

git rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
    echo [X] This folder is not a git repository.
    echo     The hidden .git folder is missing - it probably got left behind
    echo     when the folder was copied. Copy the whole folder again.
    echo.
    pause
    exit /b 1
)

echo [1/3] Repository looks good. Current state:
git log --oneline -1
git status --short
echo.

echo [2/3] Checking the remote...
git remote get-url origin >nul 2>&1
if errorlevel 1 (
    git remote add origin https://github.com/aravindhan1812/amazon-growth-engine.git
    echo       added origin
) else (
    echo       origin already set:
    git remote get-url origin
)
echo.

echo [3/3] Pushing...
echo       A browser window may open asking you to sign in to GitHub.
echo       That is GitHub asking you directly - nothing here sees your password.
echo.
git push -u origin main
if errorlevel 1 (
    echo.
    echo [X] Push failed. The two usual reasons:
    echo.
    echo     1. The repository does not exist on GitHub yet.
    echo        Create it at https://github.com/new
    echo        Name it exactly: amazon-growth-engine
    echo        Leave "Add a README file" UNTICKED, then run this again.
    echo.
    echo     2. Sign-in was cancelled, or the token you used has no repo access.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Pushed successfully.
echo.
echo One last step, in your browser:
echo     Settings -^> Pages -^> Deploy from a branch -^> Branch: main, Folder: /docs -^> Save
echo.
echo Then your dashboard goes live at:
echo     https://aravindhan1812.github.io/amazon-growth-engine/
echo.
pause
