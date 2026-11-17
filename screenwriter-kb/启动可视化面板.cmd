@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动【编剧灵感保险箱】本地可视化面板...
echo 启动后会自动打开浏览器 http://127.0.0.1:8765 ，请勿关闭此窗口。
uv run --python 3.12 python -m swkb.web_api
if errorlevel 1 (
    echo.
    echo 启动失败，请检查 uv 或 Python 3.12 环境。
    pause
)
