@echo off
rem zen - упрощённый вход в zen.py (Windows)
set DIR=%~dp0
if "%1"=="" (
  python "%DIR%zen.py" chat
) else if "%1"=="serve" (
  python "%DIR%zen.py" serve %2 %3 %4 %5
) else if "%1"=="models" (
  python "%DIR%zen.py" models
) else (
  python "%DIR%zen.py" %*
)
