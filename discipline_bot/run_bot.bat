@echo off
cd /d "%~dp0.."
python -m discipline_bot.bot run >> data\discipline\bot.log 2>&1
