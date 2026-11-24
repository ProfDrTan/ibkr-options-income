@echo off
cd /d "%~dp0.."
:loop
py -u -m discipline_bot.bot run >> data\discipline\bot.log 2>&1
timeout /t 5 /nobreak > nul
goto loop
