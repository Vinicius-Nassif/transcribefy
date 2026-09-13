<#
.SYNOPSIS
    Sobe o Transcribefy no Windows: ambiente, servidor, modelo e navegador.

.DESCRIPTION
    Um comando so para deixar a aplicacao no ar. Ele confere o ambiente (e o
    instala se ainda nao existir), sobe a interface web, baixa o modelo de fala
    em paralelo quando nenhum esta em cache e abre o navegador assim que a
    aplicacao comeca a responder.

    O servidor fica em primeiro plano: Ctrl+C encerra tudo.

    Se a porta ja estiver com o Transcribefy no ar, o script apenas abre o
    navegador nele em vez de subir um segundo servidor.

.PARAMETER Porta
    Porta da interface web. Padrao 8000.

.PARAMETER Endereco
    Em qual endereco escutar. Padrao 127.0.0.1 (so esta maquina). Use 0.0.0.0
    para alcancar a interface de outro computador da rede — lembrando que a
    pagina nao tem autenticacao nenhuma.

.PARAMETER BaixarModelo
    Apelido do modelo a baixar em paralelo (ex.: preciso, rapido). Sem isto, o
    script baixa o modelo padrao apenas quando nenhum outro esta em cache.

.PARAMETER SemBaixar
    Nao baixa modelo nenhum, mesmo que o cache esteja vazio.

.PARAMETER SemNavegador
    Nao abre o navegador.

.EXAMPLE
    .\scripts\iniciar-windows.ps1

.EXAMPLE
    .\scripts\iniciar-windows.ps1 -Porta 8080 -SemNavegador
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)] [int] $Porta = 8000,
    [string] $Endereco = "127.0.0.1",
    [string] $BaixarModelo = "",
    [switch] $SemBaixar,
    [switch] $SemNavegador
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch { }

$Raiz = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Raiz ".venv\Scripts\python.exe"
$Instalador = Join-Path $PSScriptRoot "instalar-windows.ps1"
$SEGUNDOS_ATE_RESPONDER = 90

# O navegador nunca aponta para 0.0.0.0, que nao e um endereco de destino.
$EnderecoLocal = if ($Endereco -eq "0.0.0.0") { "127.0.0.1" } else { $Endereco }
$Url = "http://${EnderecoLocal}:$Porta"
$Saude = "$Url/api/modelos"

$servidor = $null
$download = $null

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

function Porta-Livre([int] $Numero) {
    $ouvinte = $null
    try {
        $ouvinte = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback), $Numero
        $ouvinte.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($ouvinte) { $ouvinte.Stop() }
    }
}

function Transcribefy-Respondendo {
    try {
        $r = Invoke-WebRequest -Uri $Saude -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200 -and $r.Content -match '"modelo_padrao"')
    } catch {
        return $false
    }
}

function Perguntar-AoProjeto([string] $Codigo) {
    $saida = & $VenvPython -c $Codigo 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($saida | Select-Object -First 1).Trim()
}

function Encerrar-Tudo {
    if ($script:servidor -and -not $script:servidor.HasExited) {
        try { Stop-Process -Id $script:servidor.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    if ($script:download -and -not $script:download.HasExited) {
        Write-Host ""
        Escrever-Aviso "o download do modelo continuava em andamento e foi interrompido."
        try { Stop-Process -Id $script:download.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
}

# ---------------------------------------------------------------------------

try {
    Write-Host ""
    Write-Host "Transcribefy" -ForegroundColor White

    if ($PSVersionTable.Platform -and $PSVersionTable.Platform -ne "Win32NT") {
        throw "Este script e para Windows. No Linux ou WSL use: .venv/bin/transcritor web"
    }

    Escrever-Etapa "1/5  Ambiente"
    if (-not (Test-Path $VenvPython)) {
        Escrever-Aviso "ambiente ainda nao instalado; rodando o instalador primeiro"
        # O instalador lanca excecao quando algo falha, e $ErrorActionPreference
        # Stop a propaga daqui. Nao da para olhar $LASTEXITCODE: um script .ps1
        # nao o define, e o valor que sobrou do comando anterior enganaria.
        & $Instalador
        if (-not (Test-Path $VenvPython)) {
            throw "A instalacao terminou sem criar o ambiente. Rode: .\scripts\instalar-windows.cmd -Recriar"
        }
    }
    $env:PYTHONUTF8 = "1"
    $ambiente = Perguntar-AoProjeto @"
from transcritor import ffmpeg_tools, modelos, motor_whisper
dispositivo, precisao = motor_whisper.escolher_dispositivo()
cuda = motor_whisper.diagnosticar_cuda()
gpu_ignorada = '' if cuda.utilizavel or cuda.gpus < 1 else ' '.join(cuda.explicacao.split())
cache = modelos.diretorio_padrao()
prontos = [m.apelido for m in modelos.MODELOS_FALA.values() if modelos.baixado(m, cache)]
try:
    ffmpeg_tools.localizar_ffmpeg()
    ffmpeg = 'ok'
except Exception:
    ffmpeg = 'ausente'
print('|'.join([dispositivo + ' (' + precisao + ')', ffmpeg, ','.join(prontos), gpu_ignorada]))
"@
    if (-not $ambiente) { throw "O ambiente existe mas a aplicacao nao importou. Rode: .\scripts\instalar-windows.cmd -Recriar" }

    $partes = $ambiente -split '\|'
    $dispositivo, $ffmpeg, $modelosProntos, $gpuIgnorada = $partes[0], $partes[1], $partes[2], $partes[3]
    Escrever-Ok "reconhecimento em $dispositivo"
    if ($gpuIgnorada) {
        Escrever-Aviso $gpuIgnorada
        Escrever-Aviso "detalhes completos em: .venv\Scripts\transcritor.exe diagnostico"
    }
    if ($ffmpeg -eq "ok") { Escrever-Ok "ffmpeg disponivel" } else { Escrever-Aviso "ffmpeg ausente" }
    if ($modelosProntos) {
        Escrever-Ok "modelos em cache: $modelosProntos"
    } else {
        Escrever-Aviso "nenhum modelo de fala em cache ainda"
    }

    Escrever-Etapa "2/5  Porta $Porta"
    if (-not (Porta-Livre $Porta)) {
        if (Transcribefy-Respondendo) {
            Escrever-Ok "o Transcribefy ja esta no ar nesta porta"
            if (-not $SemNavegador) { Start-Process $Url }
            Write-Host ""
            Write-Host "Interface em $Url" -ForegroundColor Green
            Write-Host ""
            exit 0
        }
        throw "A porta $Porta esta ocupada por outro programa. Use -Porta com outro numero."
    }
    Escrever-Ok "livre"

    Escrever-Etapa "3/5  Modelo de fala"
    $modeloAlvo = $BaixarModelo
    if (-not $modeloAlvo -and -not $modelosProntos) {
        $modeloAlvo = Perguntar-AoProjeto "from transcritor import modelos; print(modelos.PADRAO)"
    }
    if ($SemBaixar -or -not $modeloAlvo) {
        Escrever-Ok "nada a baixar agora"
    } else {
        # Em paralelo de proposito: o download e o gargalo da primeira vez, e
        # nao ha motivo para segurar a interface enquanto ele acontece.
        $pastaDados = Join-Path $Raiz "dados"
        if (-not (Test-Path $pastaDados)) { New-Item -ItemType Directory -Path $pastaDados | Out-Null }
        $log = Join-Path $pastaDados "download-modelo.log"
        $download = Start-Process -FilePath $VenvPython `
            -ArgumentList @("-m", "transcritor", "modelos", "--baixar", $modeloAlvo) `
            -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.erro"
        Escrever-Ok "baixando '$modeloAlvo' em segundo plano"
        Write-Host "        acompanhe em $log"
    }

    Escrever-Etapa "4/5  Servidor"
    $servidor = Start-Process -FilePath $VenvPython `
        -ArgumentList @("-m", "transcritor", "web", "--host", $Endereco, "--porta", "$Porta") `
        -NoNewWindow -PassThru
    Escrever-Ok "subindo (pid $($servidor.Id))"

    $limite = (Get-Date).AddSeconds($SEGUNDOS_ATE_RESPONDER)
    $pronto = $false
    while ((Get-Date) -lt $limite) {
        if ($servidor.HasExited) {
            throw "O servidor encerrou antes de responder (codigo $($servidor.ExitCode)). A mensagem acima diz o motivo."
        }
        if (Transcribefy-Respondendo) { $pronto = $true; break }
        Start-Sleep -Milliseconds 400
    }
    if (-not $pronto) { throw "O servidor nao respondeu em $SEGUNDOS_ATE_RESPONDER s." }
    Escrever-Ok "respondendo"

    Escrever-Etapa "5/5  Navegador"
    if ($SemNavegador) {
        Escrever-Ok "pulado a pedido (-SemNavegador)"
    } else {
        Start-Process $Url
        Escrever-Ok "aberto em $Url"
    }

    Write-Host ""
    Write-Host "Transcribefy no ar em $Url" -ForegroundColor Green
    if ($Endereco -eq "0.0.0.0") {
        Escrever-Aviso "escutando em toda a rede, e a pagina nao tem autenticacao."
    }
    Write-Host "Ctrl+C encerra." -ForegroundColor White
    Write-Host ""

    Wait-Process -Id $servidor.Id
} finally {
    Encerrar-Tudo
}
