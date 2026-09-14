@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 收付明细自动生成

where py >nul 2>&1
if %errorlevel% equ 0 (
    py -3 "资金日报上传生成界面.py"
) else (
    python "资金日报上传生成界面.py"
)

if not %errorlevel% equ 0 (
    echo.
    echo 启动失败。请将本窗口截图发给开发人员。
    pause
)
