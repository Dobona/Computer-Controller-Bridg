@echo off
rem ---------------------------------------------------------------
rem  Computer Controller - one-click start with administrator rights
rem  Use this when you need to control elevated (admin) applications.
rem ---------------------------------------------------------------
cd /d "%~dp0"
call start.bat admin
