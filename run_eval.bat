@echo off
cd /d "%~dp0"
py -3 -m eval_pipeline.runner
pause
