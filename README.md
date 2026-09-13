# Transcribefy

Transcreve uma gravação do OBS **com duas faixas de áudio** e devolve uma
transcrição única, em ordem cronológica, com cada linha atribuída a um locutor:

- **Faixa 1** — o microfone do GM. Locutor único, rotulado direto.
- **Faixa 2** — os jogadores. Separada em vozes distintas por agrupamento de x-vectors.

```
**[01:12.40] GM:** vocês chegam à porta da cripta, e ela está entreaberta
**[01:18.90] Ana:** eu empurro devagar, tentando não fazer barulho
**[01:24.10] Bruno:** espera, deixa eu checar armadilhas antes
```

Construído sobre [`pytranscript`](https://pypi.org/project/pytranscript/) (Vosk +
ffmpeg + deep-translator). Roda **100% offline** depois do download inicial dos
modelos — o áudio da sua sessão nunca sai da máquina. A única exceção é a tradução
opcional, que consulta o Google Tradutor.

## Documentação

- **[Guia de uso](docs/uso.md)** — instalação, como iniciar, todas as opções,
  personalização e solução de problemas.
- **[Rodar em container](docs/docker.md)** — a mesma aplicação pelo Docker, sem
  instalar Python nem ffmpeg na máquina.
- **[Arquitetura](docs/arquitetura.md)** — organização dos módulos e pontos de
  extensão.

## Começando

```bash
# ambiente virtual com as versões fixas do requirements.txt
uv venv --python 3.12
uv pip install -r requirements.txt
uv pip install -e . --no-deps

# quais faixas o arquivo tem?
.venv/bin/transcritor faixas gravacao.mp4

# teste 5 minutos antes de rodar a sessão inteira
.venv/bin/transcritor transcrever gravacao.mp4 --inicio 600 --fim 900 -f md

# a sessão completa, com os nomes reais
.venv/bin/transcritor transcrever gravacao.mp4 --nomes "Ana,Bruno,Caio,Duda,Edu"
```

Ou pelo navegador, em <http://127.0.0.1:8000>:

```bash
.venv/bin/transcritor web
```

Os comandos ficam em `.venv/bin/`; `source .venv/bin/activate` dispensa o prefixo.
O passo a passo completo — inclusive sem o `uv` — está no
[guia de uso, seção 2](docs/uso.md#2-instalação).

### Ou por container

Sem instalar nada além do Docker:

```bash
mkdir -p midia dados saida        # coloque as gravações em midia/
docker compose up -d              # interface em http://127.0.0.1:8000

docker compose run --rm transcribefy transcrever /midia/gravacao.mp4 \
  --nomes "Ana,Bruno,Caio,Duda,Edu" -o saida/sessao-01
```

Detalhes, volumes e solução de problemas em [`docs/docker.md`](docs/docker.md).

Os modelos Vosk são baixados sob demanda na primeira execução. O ffmpeg é opcional:
sem um no sistema, a aplicação usa o binário embutido no `imageio-ffmpeg`.

Saída em `txt`, `csv`, `json`, `srt`, `vtt`, `md` e um `.detalhado.json` com início,
fim, faixa de origem e locutor de cada fala. Os arquivos vão para `saida/` pela linha
de comando e para `dados/` pela interface web, que também os oferece para download —
inclusive num navegador do Windows. Detalhes, e como gravar direto numa pasta do
Windows a partir do WSL, no [guia de uso, seção 8](docs/uso.md#8-arquivos-gerados).

## Sobre a separação de vozes

A faixa 1 não passa por diarização: é o GM, ponto. Só a faixa 2 é agrupada, com os
x-vectors de 128 dimensões do modelo `vosk-model-spk-0.4`, agrupamento hierárquico
por distância de cosseno e uma suavização temporal conservadora.

**O que esperar.** Em trechos de 1 a 2 s, a distância de cosseno fica em torno de
0,40 para o mesmo locutor e 0,74 para locutores diferentes — as distribuições se
sobrepõem, então a atribuição erra em parte das falas curtas. Num teste sintético
adversarial (quatro timbres dizendo exatamente a mesma frase) o acerto ficou entre
58% e 75%. Com cinco pessoas distintas falando coisas diferentes o resultado é bem
melhor, mas **conte com revisão manual**.

Detalhes, fatores que mais atrapalham e como calibrar:
[guia de uso, seção 9](docs/uso.md#9-ajuste-fino-da-separação-de-vozes).

## Gravando no OBS

Em **Configurações → Saída → Gravação**, marque as trilhas 1 e 2. Depois, no mixer
de áudio, use **Propriedades avançadas de áudio** para mandar o microfone do GM só
para a trilha 1 e o Discord só para a trilha 2. Se as duas fontes caírem na mesma
trilha, não há como separá-las depois.

MP4 funciona, mas **MKV é mais seguro** para sessões longas: uma queda de energia
não corrompe o arquivo, e ele ainda preserva os nomes das trilhas.

## Desenvolvimento

```bash
.venv/bin/python -m pytest tests -q
```

O `pytest` já vem no `requirements.txt`. Os testes não precisam de modelos nem de
rede. A organização dos módulos e os pontos de extensão estão em
[`docs/arquitetura.md`](docs/arquitetura.md).
