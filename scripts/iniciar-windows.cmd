@echo off
REM Sobe o Transcribefy: ambiente, servidor, modelo e navegador, de uma vez.
REM Repassa os mesmos parametros do script .ps1.
REM   iniciar-windows.cmd -Porta 8080 -SemNavegador
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar-windows.ps1" %*
if errorlevel 1 (
    echo.
    echo Nao foi possivel iniciar. A mensagem acima diz o motivo.
    pause
    exit /b 1
)
