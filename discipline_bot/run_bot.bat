@echo off
cd /d "%~dp0.."
py -u -m discipline_bot.bot run >> data\discipline\bot.log 2>&1
