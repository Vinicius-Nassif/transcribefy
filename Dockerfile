# Imagem do Transcribefy. Por padrão sobe a interface web; os comandos de linha
# de comando saem do mesmo entrypoint:
#   docker compose up -d                              -> interface web
#   docker compose run --rm transcribefy faixas ...    -> CLI
FROM python:3.12-slim

# O ffmpeg do sistema é mais rápido que o binário embutido no imageio-ffmpeg em
# arquivos grandes — e uma sessão de RPG costuma ter três horas.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TRANSCRITOR_MODELOS=/modelos

WORKDIR /app

# As dependências mudam muito menos que o código: instalá-las numa camada
# própria faz cada alteração nos módulos reaproveitar o cache do Docker.
# O requirements.txt tem as versões fixas, então a imagem é reproduzível.
# Só o requirements.txt: a imagem roda em CPU, e as bibliotecas CUDA do
# requirements-gpu.txt pesam 1,4 GB sem servir para nada aqui dentro. Numa
# máquina com GPU, transcrever fora do container é bem mais rápido.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY transcritor ./transcritor
RUN pip install -e . --no-deps

# Pontos de montagem. Criados aqui para a aplicação funcionar mesmo quando o
# container sobe sem nenhum volume.
#   /modelos    cache dos modelos de fala (3 GB no 'preciso' — sempre um volume)
#   /midia      vídeos de entrada, montados somente para leitura
#   /app/dados  trabalhos enviados pela interface web
#   /app/saida  transcrições geradas pela CLI
RUN useradd --create-home --uid 1000 transcritor \
    && mkdir -p /modelos /midia /app/dados /app/saida \
    && chown -R transcritor:transcritor /modelos /app

USER transcritor

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/modelos', timeout=4)"

ENTRYPOINT ["transcritor"]
# Dentro do container, 127.0.0.1 só responde ao próprio container: o servidor
# precisa ligar em 0.0.0.0 para a porta publicada chegar ao navegador.
CMD ["web", "--host", "0.0.0.0"]
