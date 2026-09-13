<#
.SYNOPSIS
    Instala o Transcribefy e todas as dependencias no Windows 11.

.DESCRIPTION
    Cria o ambiente virtual em .venv, instala as dependencias fixadas no
    requirements.txt, registra o projeto (o que cria o comando `transcritor`) e,
    quando ha uma GPU NVIDIA, instala tambem as bibliotecas CUDA.

    Nao precisa instalar ffmpeg: a aplicacao usa o binario que vem junto com o
    pacote imageio-ffmpeg.

.PARAMETER Gpu
    auto (padrao) instala as bibliotecas CUDA se encontrar uma GPU NVIDIA.
    sim forca a instalacao; nao pula, deixando tudo em CPU.

.PARAMETER Python
    Caminho de um python.exe especifico. Sem isto, o script procura sozinho.

.PARAMETER BaixarModelo
    Apelido de um modelo para baixar ja na instalacao (ex.: preciso, rapido).

.PARAMETER Recriar
    Apaga um .venv existente antes de comecar.

.EXAMPLE
    .\scripts\instalar-windows.ps1

.EXAMPLE
    .\scripts\instalar-windows.ps1 -Gpu nao -BaixarModelo rapido
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet("auto", "sim", "nao")] [string] $Gpu = "auto",
    [string] $Python = "",
    [string] $BaixarModelo = "",
    [switch] $Recriar
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

# O script imprime acentos; sem isto o PowerShell 5.1 os embaralha no console.
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch { }

$VERSAO_MINIMA = [version] "3.12"
$Raiz = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Raiz ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

function Escrever-Etapa([string] $Texto) {
    Write-Host ""
    Write-Host "==> $Texto" -ForegroundColor Cyan
}

function Escrever-Ok([string] $Texto) {
    Write-Host "    OK  $Texto" -ForegroundColor Green
}

function Escrever-Aviso([string] $Texto) {
    Write-Host "    !   $Texto" -ForegroundColor Yellow
}

function Invocar([string] $Exe, [string[]] $Argumentos, [string] $Falha) {
    & $Exe @Argumentos
    if ($LASTEXITCODE -ne 0) {
        throw "$Falha (codigo $LASTEXITCODE)"
    }
}

function Obter-VersaoPython([string] $Exe, [string[]] $Prefixo) {
    $codigo = "import sys; print('{}.{}'.format(*sys.version_info[:2]))"
    try {
        $saida = & $Exe @($Prefixo + @("-c", $codigo)) 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $saida) { return $null }
        return [version] ($saida | Select-Object -First 1).Trim()
    } catch {
        return $null
    }
}

function Encontrar-Python {
    if ($Python) {
        $versao = Obter-VersaoPython $Python @()
        if (-not $versao) { throw "Nao consegui executar '$Python'." }
        if ($versao -lt $VERSAO_MINIMA) {
            throw "'$Python' e Python $versao; o projeto exige $VERSAO_MINIMA ou mais novo."
        }
        return @{ Exe = $Python; Prefixo = @(); Versao = $versao }
    }

    # O py.exe e o jeito confiavel de escolher a versao quando ha varias
    # instaladas; o 'python' solto pode ser o atalho da Microsoft Store.
    $candidatos = @(
        @{ Exe = "py";     Prefixo = @("-3.13") },
        @{ Exe = "py";     Prefixo = @("-3.12") },
        @{ Exe = "py";     Prefixo = @("-3") },
        @{ Exe = "python"; Prefixo = @() },
        @{ Exe = "python3"; Prefixo = @() }
    )
    foreach ($c in $candidatos) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $versao = Obter-VersaoPython $c.Exe $c.Prefixo
        if ($versao -and $versao -ge $VERSAO_MINIMA) {
            return @{ Exe = $c.Exe; Prefixo = $c.Prefixo; Versao = $versao }
        }
    }
    throw @"
Nao encontrei Python $VERSAO_MINIMA ou mais novo.

Instale com:
    winget install --id Python.Python.3.12 --source winget

Feche e reabra o terminal depois e rode este script de novo.
"@
}

function Perguntar-AoProjeto([string] $Codigo) {
    $saida = & $VenvPython -c $Codigo 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $saida) { return $null }
    return ($saida | Select-Object -First 1).Trim()
}

function Gpus-Visiveis {
    # Pergunta ao CTranslate2, que e quem de fato decide usar a GPU na hora de
    # transcrever. Procurar o nvidia-smi no PATH responderia outra pergunta, e
    # as duas respostas divergem: da para ter placa visivel sem o nvidia-smi no
    # caminho, e ai as bibliotecas CUDA nao seriam instaladas mas seriam usadas.
    $n = Perguntar-AoProjeto "import ctranslate2; print(ctranslate2.get_cuda_device_count())"
    if (-not $n) { return 0 }
    return [int] $n
}

# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Transcribefy - instalacao no Windows" -ForegroundColor White
Write-Host "Projeto em: $Raiz"

if ($PSVersionTable.Platform -and $PSVersionTable.Platform -ne "Win32NT") {
    throw "Este script e para Windows. No Linux ou WSL, veja docs/uso.md, secao 2.2."
}

Escrever-Etapa "1/6  Procurando o Python"
$py = Encontrar-Python
$comandoPython = (@($py.Exe) + $py.Prefixo) -join " "
Escrever-Ok "Python $($py.Versao) via '$comandoPython'"

Escrever-Etapa "2/6  Preparando o ambiente virtual"
if ($Recriar -and (Test-Path $Venv)) {
    Remove-Item -Recurse -Force $Venv
    Escrever-Ok ".venv anterior removido"
}
if (Test-Path $VenvPython) {
    Escrever-Ok ".venv ja existe (use -Recriar para comecar do zero)"
} else {
    Invocar $py.Exe ($py.Prefixo + @("-m", "venv", $Venv)) "Falha ao criar o ambiente virtual"
    Escrever-Ok "criado em $Venv"
}
if (-not (Test-Path $VenvPython)) {
    throw "O ambiente virtual nao tem Scripts\python.exe. Rode de novo com -Recriar."
}

Escrever-Etapa "3/6  Atualizando o pip"
Invocar $VenvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip") "Falha ao atualizar o pip"
Escrever-Ok "pip atualizado"

Escrever-Etapa "4/6  Instalando as dependencias (alguns minutos na primeira vez)"
Invocar $VenvPython @("-m", "pip", "install", "-r", (Join-Path $Raiz "requirements.txt")) `
    "Falha ao instalar as dependencias"
Invocar $VenvPython @("-m", "pip", "install", "-e", $Raiz, "--no-deps") `
    "Falha ao registrar o projeto no ambiente"
Escrever-Ok "dependencias instaladas e comando 'transcritor' registrado"

Escrever-Etapa "5/6  Bibliotecas CUDA (opcional)"
$gpus = if ($Gpu -eq "nao") { 0 } else { Gpus-Visiveis }
if ($gpus -gt 0) { Escrever-Ok "$gpus GPU(s) NVIDIA visiveis" }

$instalarGpu = switch ($Gpu) {
    "sim"  { $true }
    "nao"  { $false }
    default { $gpus -gt 0 }
}
if ($instalarGpu) {
    Write-Host "    Baixando ~1,4 GB de bibliotecas CUDA..."
    Invocar $VenvPython @("-m", "pip", "install", "-r", (Join-Path $Raiz "requirements-gpu.txt")) `
        "Falha ao instalar as bibliotecas CUDA"
    Escrever-Ok "CUDA instalada"
} elseif ($Gpu -eq "nao") {
    Escrever-Ok "pulado a pedido (-Gpu nao); tudo roda em CPU"
} else {
    Escrever-Aviso "nenhuma GPU NVIDIA visivel; tudo roda em CPU (mais lento, mesmo resultado)"
}

Escrever-Etapa "6/6  Conferindo a instalacao"
# O proprio comando de diagnostico faz a conferencia: uma fonte de verdade so,
# e o usuario pode repetir o mesmo comando depois para comparar.
$env:PYTHONUTF8 = "1"
$resultado = & $VenvPython -m transcritor diagnostico
if ($LASTEXITCODE -ne 0) { throw "A instalacao terminou, mas a aplicacao nao importou." }
foreach ($linha in $resultado) { Write-Host "    $linha" }

if ($instalarGpu -and ($resultado -match "Dispositivo cpu")) {
    Write-Host ""
    Escrever-Aviso "as bibliotecas CUDA foram instaladas, mas a GPU nao sera usada."
    Escrever-Aviso "a linha 'GPU' acima diz o motivo. A transcricao funciona em CPU,"
    Escrever-Aviso "so que bem mais devagar."
}

if ($BaixarModelo) {
    Escrever-Etapa "Baixando o modelo '$BaixarModelo'"
    Invocar $VenvPython @("-m", "transcritor", "modelos", "--baixar", $BaixarModelo) `
        "Falha ao baixar o modelo"
    Escrever-Ok "modelo pronto"
}

Write-Host ""
Write-Host "Pronto." -ForegroundColor Green
Write-Host ""
Write-Host "Interface no navegador:"
Write-Host "    .venv\Scripts\transcritor.exe web" -ForegroundColor White
Write-Host ""
Write-Host "Linha de comando (melhor para a sessao inteira):"
Write-Host "    .venv\Scripts\transcritor.exe faixas C:\caminho\gravacao.mp4" -ForegroundColor White
Write-Host "    .venv\Scripts\transcritor.exe transcrever C:\caminho\gravacao.mp4 -o saida\sessao-01" -ForegroundColor White
Write-Host ""
Write-Host "Para chamar so 'transcritor', ative o ambiente antes:"
Write-Host "    .venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host ""
Write-Host "O guia completo esta em docs\uso.md."
Write-Host ""
