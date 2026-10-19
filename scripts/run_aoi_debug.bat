@echo off
REM AOI Recipe Verification - run (debug) with a console so errors are visible.
REM Use this if the app does not start / the window closes instantly.
REM Same launcher as run_aoi.bat (applies a staged update first), console mode.
call "%~dp0run_aoi.bat" debug
