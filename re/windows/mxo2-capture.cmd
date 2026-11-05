@echo off
rem Double-click to start the MXO2 register capture helper (asks for admin rights).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mxo2-capture.ps1" %*
