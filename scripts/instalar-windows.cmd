@echo off
REM Atalho para quem prefere clicar duas vezes ou nao quer mexer na politica de
REM execucao do PowerShell. Repassa os mesmos parametros do script .ps1.
REM   instalar-windows.cmd -Gpu nao -BaixarModelo rapido
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar-windows.ps1" %*
if errorlevel 1 (
    echo.
    echo A instalacao falhou. A mensagem acima diz o motivo.
    pause
    exit /b 1
)
pause
