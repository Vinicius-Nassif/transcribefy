# CLAUDE.md

Regras de trabalho deste projeto (Transcribefy). A documentação técnica está em
`docs/`: `arquitetura.md` (módulos, API, pontos de extensão, testes), `uso.md` e
`docker.md`.

## Como trabalhar

- Responda sempre em português.
- Trabalhe uma fase (ou um bloco) por vez. Ao fim de cada uma: rode todos os
  testes, mostre um resumo do que mudou e proponha a mensagem de commit.
- Antes de mudar código sem cobertura, escreva os testes dele.
- Quando surgir uma decisão de design não prevista, pare e pergunte.
- Testes: `.venv/bin/python -m pytest tests -q`. Não precisam de modelos nem de
  rede, e rodam no CI (Linux e Windows) a cada push e pull request.

## Git e GitHub

- Commit só depois da aprovação do usuário.
- Nunca `git add .` nem `git add -A`: adicione os arquivos pelo nome e mostre o
  `git status` antes de cada commit.
- Antes de alterar arquivos, confira o branch. Se for a `main`, pergunte se deve
  criar um branch novo e sugira um nome.
- Depois de cada commit aprovado, pergunte se deve enviar ao GitHub. Se o branch já
  estiver lá sem PR, pergunte se deve abrir um, e se em rascunho.
- Com PR aberto, a cada envio atualize a descrição e as checkboxes: só marque o
  que foi totalmente entregue e enviado; o que falta fica desmarcado com uma nota
  curta dizendo onde será feito.
- Nunca faça merge na `main`, nem push direto nela, sem pedido explícito. Force
  push só com autorização explícita.

## Dependências

- Antes de adicionar uma dependência, diga qual é, para que serve e se há
  alternativa sem dependência nova. Só adicione com aprovação.
- Registre de forma consistente: versão exata (`==`) nos `requirements*.txt`
  aplicáveis (inclusive o `requirements-gpu.txt`, se for o caso) e versão mínima
  (`>=`) no `pyproject.toml`.
- Confira se o `Dockerfile` e os scripts do Windows (`scripts/*-windows.ps1`)
  precisam de ajuste, e se o `tests/test_instalacao.py` continua passando.

## Dados do usuário e serviços externos

- Nunca rode comandos que apagam ou alteram dados nas pastas reais do projeto
  (`dados/`, `saida/`, `midia/`, `.medicoes/`) sem perguntar. Para testar esse
  tipo de comando, use pastas temporárias. A interface web grava em `dados/`
  relativo à pasta de onde roda: para um teste manual, suba o servidor a partir de
  uma pasta temporária.
- O `tests/conftest.py` já redireciona `dados/` para uma pasta temporária em todos
  os testes; mantenha assim.
- Não faça chamadas reais ao Google Tradutor sem pedido do usuário: ele já
  bloqueou esta máquina por excesso de requisições. Nos testes, use motores de
  tradução falsos.

## Estado atual

O progresso, as decisões tomadas e as pendências estão no PR #2
(`melhorias-faixas-interface` → `main`): https://github.com/Vinicius-Nassif/transcribefy/pull/2.
Ele é a fonte da verdade; confira a descrição antes de continuar o trabalho.
