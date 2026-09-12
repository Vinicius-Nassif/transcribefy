# Transcritor de Sessões

Transcreve uma gravação do OBS em MP4 **com duas faixas de áudio** e devolve uma
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

---

## Instalação

```bash
uv venv --python 3.12
uv pip install -e .
```

Também é preciso ter o **ffmpeg**. Se não houver um no sistema, a aplicação usa
automaticamente o binário embutido no `imageio-ffmpeg` (já instalado como
dependência). Para um ffmpeg completo do sistema:

```bash
sudo apt install ffmpeg
```

Os modelos Vosk são baixados sob demanda na primeira execução e ficam em
`~/.cache/transcritor-rpg/modelos` (mude com `TRANSCRITOR_MODELOS`).

| Apelido      | Tamanho | Observação                              |
|--------------|---------|-----------------------------------------|
| `pt-pequeno` | ~31 MB  | padrão; rápido, precisão menor          |
| `pt-grande`  | ~1,6 GB | bem mais preciso, bem mais lento        |
| `en-pequeno` | ~40 MB  | inglês                                  |
| `en-grande`  | ~1,8 GB | inglês                                  |
| `locutor`    | ~13 MB  | x-vectors; baixado sempre               |

---

## Interface web

```bash
transcritor web
# http://127.0.0.1:8000
```

Envie o MP4, confira as faixas detectadas, ajuste os nomes e acompanhe o
progresso. Ao final, baixe a transcrição em qualquer formato.

## Linha de comando

```bash
# quais faixas o arquivo tem?
transcritor faixas sessao.mp4

# transcrição completa
transcritor transcrever sessao.mp4 -o saida/sessao-01

# o GM continua "GM"; os jogadores ganham nome, na ordem em que falam pela primeira vez
transcritor transcrever sessao.mp4 --nomes "Ana,Bruno,Caio,Duda,Edu"

# teste 5 minutos antes de rodar as 3 horas inteiras
transcritor transcrever sessao.mp4 --inicio 600 --fim 900 -f md

# modelo grande + tradução para inglês
transcritor transcrever sessao.mp4 -m pt-grande --traduzir en
```

### Opções principais

| Opção | Para quê |
|---|---|
| `-o, --saida` | caminho de saída sem extensão (padrão `saida/<nome-do-video>`) |
| `-m, --modelo` | apelido da tabela acima ou caminho de um modelo Vosk |
| `--faixa-gm` / `--faixa-grupo` | números das faixas (1 e 2 por padrão) |
| `--nome-gm` | como chamar o locutor da faixa 1 (padrão `GM`) |
| `-n, --jogadores` | quantas vozes há na faixa 2 (`5` por padrão, ou `auto`) |
| `--nomes` | nomes dos jogadores, separados por vírgula |
| `--inicio` / `--fim` | transcreve apenas um trecho, em segundos |
| `--deslocamento` | corrige dessincronia da faixa 2, em segundos |
| `--sem-juntar` | não agrupa falas seguidas do mesmo locutor |
| `--traduzir` | gera também a versão traduzida (ex.: `en`) |
| `-f, --formatos` | `txt,csv,json,srt,vtt,md` |

### Formatos gerados

`txt` `csv` `json` `srt` `vtt` vêm do `pytranscript`; `md` e `.detalhado.json`
são deste projeto. O `.detalhado.json` é o mais completo — traz início, fim,
faixa de origem e locutor de cada fala, pronto para pós-processamento.

As legendas `srt`/`vtt` usam o **tempo final real** de cada fala, e não a
estimativa fixa de 5 segundos do `pytranscript`.

---

## Gravando no OBS

Em **Configurações → Saída → Gravação**, marque as trilhas 1 e 2. Depois, no
mixer de áudio, use o ícone de engrenagem → **Propriedades avançadas de áudio**
para mandar o microfone do GM só para a trilha 1 e o áudio do Discord só para a
trilha 2. Se as duas fontes caírem na mesma trilha, não há como separá-las depois.

MP4 funciona, mas **MKV é mais seguro** para sessões longas (uma queda de energia
não corrompe o arquivo) e ainda preserva os nomes das trilhas, que aparecem no
`transcritor faixas`.

---

## Sobre a separação de vozes

A faixa 1 não passa por diarização: é o GM, ponto. Só a faixa 2 é agrupada, com
os x-vectors de 128 dimensões do modelo `vosk-model-spk-0.4`, agrupamento
hierárquico por distância de cosseno (ligação média) e uma suavização temporal
conservadora — uma fala curta cercada por duas do mesmo locutor passa para ele,
mas só quando a própria voz dela não indica o contrário com folga.

**O que esperar.** Em medições sobre trechos de 1 a 2 s, a distância de cosseno
fica em torno de 0,40 para o mesmo locutor e 0,74 para locutores diferentes — as
distribuições se sobrepõem, então a atribuição erra em parte das falas curtas.
Num teste sintético adversarial (quatro timbres dizendo exatamente a mesma frase)
o acerto ficou entre 58% e 75%. Com cinco pessoas distintas falando coisas
diferentes o resultado é bem melhor, mas **conte com revisão manual**. Fatores que
mais atrapalham:

- **Fala sobreposta** — duas pessoas ao mesmo tempo geram um vetor misturado.
- **Falas de uma ou duas palavras** — vetor curto demais para ser estável.
- **Microfones muito diferentes** entre os jogadores ajudam; vozes parecidas com o
  mesmo headset atrapalham.

Dicas práticas: informe o número exato de locutores (`-n 5`) em vez de `auto`, e
use `--inicio/--fim` para calibrar num trecho curto antes de rodar a sessão toda.

---

## Desenvolvimento

```bash
uv pip install pytest
python -m pytest tests -q
```

Os testes não precisam de modelos nem de rede.

| Módulo | Responsabilidade |
|---|---|
| `ffmpeg_tools.py` | acha o ffmpeg, lê as faixas do container, extrai WAV 16 kHz mono |
| `modelos.py` | baixa e faz cache dos modelos Vosk |
| `transcricao.py` | roda o Vosk guardando tempo inicial/final e x-vector por fala |
| `diarizacao.py` | agrupa os x-vectors em locutores |
| `mesclagem.py` | intercala as duas faixas numa linha do tempo só |
| `saida.py` | gera os arquivos, estendendo o `Transcript` do `pytranscript` |
| `pipeline.py` | orquestra tudo e reporta progresso |
| `cli.py` / `web.py` | as duas interfaces |
