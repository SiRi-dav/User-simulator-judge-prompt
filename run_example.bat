@echo off
cd /d "%~dp0"
py -3 -m eval_pipeline.runner --dialogues "eval_pipeline\examples\dialogues" --cases "eval_pipeline\examples\cases" --output "example_results"
pause
