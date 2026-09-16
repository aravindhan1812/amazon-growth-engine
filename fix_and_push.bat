@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   Repair and push: amazon-growth-engine
echo ==================================================
echo Folder: %cd%
echo.

REM ---------- 1. git present? ----------
git --version >nul 2>&1
if errorlevel 1 (
    echo [X] Git is not installed.
    echo     Get it from https://git-scm.com/downloads then run this again.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('git --version') do echo Using %%v
echo.

REM ---------- 2. are the project files actually here? ----------
if not exist "run_simulation.py" (
    echo [X] run_simulation.py is missing, so this is not the project folder.
    echo     Put this file inside the amazon-growth-engine folder and run it there.
    echo.
    pause
    exit /b 1
)
if not exist "docs\index.html" (
    echo [!] docs\index.html is missing - GitHub Pages will have nothing to serve.
    echo     Run: python run_simulation.py  then: python build_dashboard.py
    echo     Continuing anyway.
    echo.
)

REM ---------- 3. where does git think it is? ----------
set "TOPLEVEL="
for /f "delims=" %%i in ('git rev-parse --show-toplevel 2^>nul') do set "TOPLEVEL=%%i"
if defined TOPLEVEL (
    echo Git repository root: !TOPLEVEL!
) else (
    echo No git repository found in this folder.
)
echo.

REM ---------- 4. any commits? if not, build the repo from these files ----------
git rev-parse --verify HEAD >nul 2>&1
if errorlevel 1 (
    echo [*] No commits found - creating the repository from the files in this folder.
    if not exist ".git" git init
    git symbolic-ref HEAD refs/heads/main
    git config user.name "aravindhan1812"
    git config user.email "work.aravindhprakash@gmail.com"
    git add -A
    git commit -m "Amazon growth engine MVP: four coordinated optimisation layers"
    if errorlevel 1 (
        echo [X] Could not create the commit. Paste the message above into the chat.
        echo.
        pause
        exit /b 1
    )
    echo     created the initial commit
) else (
    echo [*] Existing commits found - keeping the history.
)

REM ---------- 5. make sure the branch is called main ----------
git branch -M main
echo.
echo Branch and history:
git branch -v
git log --oneline
echo.

REM ---------- 6. remote ----------
git remote get-url origin >nul 2>&1
if errorlevel 1 (
    git remote add origin https://github.com/aravindhan1812/amazon-growth-engine.git
    echo Remote added.
) else (
    git remote set-url origin https://github.com/aravindhan1812/amazon-growth-engine.git
    echo Remote updated.
)
git remote -v
echo.

REM ---------- 7. push ----------
echo Pushing to GitHub...
echo A browser window may open for you to sign in. That is GitHub asking you directly.
echo.
git push -u origin main
if errorlevel 1 (
    echo.
    echo [X] Push failed. Read the error above, then:
    echo.
    echo   "Repository not found"       - create it at https://github.com/new
    echo                                  named exactly amazon-growth-engine,
    echo                                  with "Add a README file" UNTICKED.
    echo   "Authentication failed"      - sign-in was cancelled or the token
    echo                                  has no repo access.
    echo   "rejected / fetch first"     - the GitHub repo already has commits.
    echo                                  Run: git push -u origin main --force
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Pushed successfully.
echo.
echo Last step, in your browser:
echo     Settings -^> Pages -^> Deploy from a branch -^> Branch: main, Folder: /docs -^> Save
echo.
echo Then your dashboard is live at:
echo     https://aravindhan1812.github.io/amazon-growth-engine/
echo.
pause
