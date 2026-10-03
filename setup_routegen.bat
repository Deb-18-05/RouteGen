@echo off
setlocal

echo Initializing RouteGen...

mkdir routegen 2>nul
mkdir data 2>nul
mkdir output 2>nul
mkdir tests 2>nul

mkdir routegen\station 2>nul
mkdir routegen\mapdata 2>nul
mkdir routegen\routing 2>nul
mkdir routegen\railway 2>nul
mkdir routegen\topology 2>nul
mkdir routegen\terrain 2>nul
mkdir routegen\scenery 2>nul
mkdir routegen\export 2>nul

type nul > routegen\__init__.py
type nul > routegen\main.py
type nul > routegen\station\__init__.py
type nul > routegen\mapdata\__init__.py
type nul > routegen\routing\__init__.py
type nul > routegen\railway\__init__.py
type nul > routegen\topology\__init__.py
type nul > routegen\terrain\__init__.py
type nul > routegen\scenery\__init__.py
type nul > routegen\export\__init__.py

type nul > requirements.txt
type nul > README.md

echo.
echo RouteGen initialized successfully.
echo.

tree /F

pause