#!/usr/bin/env bash
# Teste de fumaça da imagem do container: constrói, exercita as duas interfaces
# e confere o contrato de volumes, permissões e rede.
#
#   scripts/teste-container.sh [--sem-build] [--limpar] [-h]
#
# Roda numa pasta própria (.teste-container/), num volume próprio e numa porta
# livre, então não encosta em midia/, dados/, saida/ nem no container do compose.
set -euo pipefail

IMAGEM="${IMAGEM:-transcribefy:local}"
VOLUME="${VOLUME:-transcribefy-teste-modelos}"
MODELO="${MODELO:-pt-pequeno}"
CONTAINER="transcribefy-teste-$$"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRABALHO="$RAIZ/.teste-container"
DONO="$(id -u):$(id -g)"

BUILD=1
LIMPAR=0
while [ $# -gt 0 ]; do
    case "$1" in
        --sem-build) BUILD=0 ;;
        --limpar) LIMPAR=1 ;;
        -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opção desconhecida: $1" >&2; exit 2 ;;
    esac
    shift
done

TOTAL=0
FALHAS=0

etapa()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()     { TOTAL=$((TOTAL + 1)); printf '  \033[32m✓\033[0m %s\n' "$1"; }
falha()  { TOTAL=$((TOTAL + 1)); FALHAS=$((FALHAS + 1)); printf '  \033[31m✗\033[0m %s\n      %s\n' "$1" "$2"; }

# conferir <descrição> <trecho esperado> <valor obtido>
conferir() {
    case "$3" in
        *"$2"*) ok "$1" ;;
        *)      falha "$1" "esperava conter '$2', veio: $(echo "$3" | tr '\n' ' ' | cut -c1-160)" ;;
    esac
}

encerrar() {
    local codigo=$?
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$TRABALHO"
    [ "$LIMPAR" = 1 ] && docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    return $codigo
}
trap encerrar EXIT

# Um container só, sem porta publicada: usado nos testes de linha de comando.
avulso() {
    docker run --rm --user "$DONO" \
        -v "$VOLUME:/modelos" \
        -v "$TRABALHO/midia:/midia:ro" \
        -v "$TRABALHO/dados:/app/dados" \
        -v "$TRABALHO/saida:/app/saida" \
        "$@"
}

porta_livre() {
    local p
    for p in $(seq 8099 8120); do
        (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && exec 3>&- || { echo "$p"; return 0; }
    done
    echo "Nenhuma porta livre entre 8099 e 8120." >&2
    return 1
}

# --------------------------------------------------------------------------
etapa "0. Pré-requisitos"
command -v docker >/dev/null || { echo "docker não encontrado. No WSL, ative a integração no Docker Desktop." >&2; exit 1; }
command -v curl   >/dev/null || { echo "curl não encontrado; ele é usado nos testes da interface web." >&2; exit 1; }
docker info >/dev/null 2>&1  || { echo "o daemon do Docker não está respondendo." >&2; exit 1; }
ok "docker, curl e o daemon respondem"

cd "$RAIZ"
if docker compose config -q 2>/dev/null; then
    ok "docker-compose.yml é válido e as variáveis resolvem"
else
    falha "docker-compose.yml é válido" "$(docker compose config -q 2>&1 | tail -2)"
fi

if [ "$BUILD" = 1 ]; then
    etapa "1. Construindo a imagem"
    docker compose build >/dev/null 2>&1 && ok "imagem construída" || falha "imagem construída" "veja: docker compose build"
else
    etapa "1. Build pulado (--sem-build)"
fi
docker image inspect "$IMAGEM" >/dev/null 2>&1 || { echo "Imagem $IMAGEM não existe. Rode sem --sem-build." >&2; exit 1; }
conferir "a imagem tem tamanho plausível (> 200 MB)" "sim" \
    "$([ "$(docker image inspect "$IMAGEM" --format '{{.Size}}')" -gt 200000000 ] && echo sim || echo nao)"

etapa "2. Preparando a área de teste"
rm -rf "$TRABALHO"
mkdir -p "$TRABALHO/midia" "$TRABALHO/dados" "$TRABALHO/saida"
docker volume create "$VOLUME" >/dev/null
# O volume nasce com o dono da imagem (UID 1000); ajusta para quem roda o teste.
docker run --rm --user 0 -v "$VOLUME:/modelos" --entrypoint chown "$IMAGEM" -R "$DONO" /modelos >/dev/null 2>&1 || true
# O vídeo de teste sai do ffmpeg da própria imagem: 6 s, duas faixas de tons.
docker run --rm --user "$DONO" -v "$TRABALHO/midia:/trabalho" --entrypoint ffmpeg "$IMAGEM" \
    -y -loglevel error \
    -f lavfi -i color=c=navy:s=320x240:d=6 \
    -f lavfi -i "sine=frequency=220:duration=6" \
    -f lavfi -i "sine=frequency=440:duration=6" \
    -map 0:v -map 1:a -map 2:a \
    -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
    /trabalho/teste-2-faixas.mp4 >/dev/null 2>&1 || true
[ -s "$TRABALHO/midia/teste-2-faixas.mp4" ] \
    && ok "vídeo de teste com duas faixas gerado pelo ffmpeg da imagem" \
    || falha "vídeo de teste gerado" "o arquivo não apareceu"

etapa "3. Linha de comando"
conferir "o entrypoint é o transcritor" "Modelos de fala" "$(avulso "$IMAGEM" modelos 2>&1)"
conferir "o cache aponta para o volume" "Cache: /modelos" "$(avulso "$IMAGEM" modelos 2>&1)"
conferir "lê as duas faixas do vídeo" "2 faixa(s) de áudio" "$(avulso "$IMAGEM" faixas /midia/teste-2-faixas.mp4 2>&1)"
conferir "usa o ffmpeg do sistema, não o embutido" "/usr/bin/ffmpeg" \
    "$(avulso --entrypoint sh "$IMAGEM" -c 'command -v ffmpeg' 2>&1)"
conferir "/midia é somente leitura" "Read-only file system" \
    "$(avulso --entrypoint sh "$IMAGEM" -c 'touch /midia/invasor' 2>&1 || true)"

etapa "4. Transcrição completa (baixa ~44 MB de modelos na primeira vez)"
saida_cli="$(avulso "$IMAGEM" transcrever /midia/teste-2-faixas.mp4 -m "$MODELO" -f md,json -o saida/teste 2>&1 || true)"
conferir "o pipeline chega ao fim" "Arquivos gerados" "$saida_cli"
for ext in md json detalhado.json; do
    [ -s "$TRABALHO/saida/teste.$ext" ] \
        && ok "saida/teste.$ext apareceu no host" \
        || falha "saida/teste.$ext apareceu no host" "arquivo ausente ou vazio"
done
conferir "os arquivos pertencem a quem rodou o teste" "$(id -u)" \
    "$(stat -c '%u' "$TRABALHO/saida/teste.md" 2>/dev/null || echo '?')"
conferir "os modelos ficaram no volume" "* $MODELO" "$(avulso "$IMAGEM" modelos 2>&1)"

etapa "5. Interface web"
PORTA="$(porta_livre)"
docker run -d --name "$CONTAINER" --user "$DONO" \
    -p "127.0.0.1:$PORTA:8000" \
    -v "$VOLUME:/modelos" \
    -v "$TRABALHO/midia:/midia:ro" \
    -v "$TRABALHO/dados:/app/dados" \
    -v "$TRABALHO/saida:/app/saida" \
    "$IMAGEM" >/dev/null
estado=""
for _ in $(seq 1 30); do
    estado="$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo ausente)"
    [ "$estado" = "healthy" ] && break
    [ "$estado" = "unhealthy" ] && break
    sleep 2
done
conferir "o healthcheck fica saudável" "healthy" "$estado"
conferir "a página inicial responde" "200" \
    "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORTA/" || true)"
conferir "a API lista os modelos" '"apelido"' "$(curl -s "http://127.0.0.1:$PORTA/api/modelos" || true)"

# O formulário manda 'fim' vazio quando não há corte; um 0 aqui significaria
# "terminar no segundo zero" e o ffmpeg recusaria.
resposta="$(curl -s -X POST "http://127.0.0.1:$PORTA/api/trabalhos" \
    -F "video=@$TRABALHO/midia/teste-2-faixas.mp4" \
    -F "modelo=$MODELO" -F "faixa_gm=1" -F "faixa_grupo=2" -F "nome_gm=GM" \
    -F "jogadores=2" -F "nomes=" -F "formatos=md,json" -F "idioma=pt" \
    -F "traduzir=" -F "deslocamento=0" -F "inicio=0" -F "fim=" \
    -F "sem_juntar=false" || true)"
trabalho_id="$(echo "$resposta" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')"
if [ -z "$trabalho_id" ]; then
    falha "o envio do vídeo é aceito" "resposta: $(echo "$resposta" | cut -c1-160)"
else
    ok "o envio do vídeo é aceito"
    estado_trabalho=""
    for _ in $(seq 1 60); do
        corpo="$(curl -s "http://127.0.0.1:$PORTA/api/trabalhos/$trabalho_id" || true)"
        estado_trabalho="$(echo "$corpo" | sed -n 's/.*"estado":"\([^"]*\)".*/\1/p')"
        case "$estado_trabalho" in concluido|erro) break ;; esac
        sleep 2
    done
    conferir "a transcrição conclui" "concluido" "${estado_trabalho:-sem resposta}${corpo:+ | $(echo "$corpo" | sed -n 's/.*"erro":"\([^"]*\)".*/\1/p')}"
    [ -s "$TRABALHO/dados/trabalhos/$trabalho_id/teste-2-faixas.md" ] \
        && ok "os arquivos do trabalho aparecem em dados/ no host" \
        || falha "os arquivos do trabalho aparecem em dados/ no host" "nada em dados/trabalhos/$trabalho_id"
    conferir "o download do resultado funciona" "200" \
        "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORTA/api/trabalhos/$trabalho_id/arquivos/teste-2-faixas.md" || true)"
    conferir "não dá para sair da pasta do trabalho" "404" \
        "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORTA/api/trabalhos/$trabalho_id/arquivos/..%2F..%2F..%2Fetc%2Fpasswd" || true)"
fi

etapa "Resumo"
if [ "$FALHAS" = 0 ]; then
    printf '  \033[32m%d de %d verificações passaram.\033[0m\n\n' "$TOTAL" "$TOTAL"
    exit 0
fi
printf '  \033[31m%d de %d verificações falharam.\033[0m\n\n' "$FALHAS" "$TOTAL"
exit 1
