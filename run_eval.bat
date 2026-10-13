@echo off
cd /d "%~dp0"
if not exist "data\dialogues" mkdir "data\dialogues"
if not exist "data\cases" mkdir "data\cases"
if not exist "data\real_dialogues" mkdir "data\real_dialogues"
py -3 -m eval_pipeline.runner
pause
