# ACE · Agente Contratação (Excel)

Mini-programa separado do CRT principal. Roda no PC que tem a planilha
`PRODUTIVIDADE CONTRATAÇÃO.xlsx`.

## O que faz

1. Sempre busca a planilha na **Área de Trabalho** do PC (`Desktop` / OneDrive Desktop)
2. Lê a aba do mês vigente (ex.: `08 2026`) com janela **ontem + hoje**
3. Ignora linhas `CANCELADO`
4. Agrega por placa do **CAVALO** (frete fechado = custo)
5. Atualiza `dashboard/data/contratacao/` e envia para Google Sheets

O frete SSW **200** fica só no **CRT** principal — esta extensão não baixa 200.

## Instalação no PC da planilha

1. Copie a pasta `extensao_contratacao` (ou rode `ctr agente update` no ACE principal apontando `ctr_agente_dir`)
2. Garanta que o ACE completo está acessível (pasta pai) — Sheets/credenciais
3. Ajuste `config_agente.json` (copie do `.example`)
4. Execute `run_agente.bat` ou `python -m extensao_contratacao.agent_main`

## Comandos úteis

```bat
python -m extensao_contratacao.agent_main
python -m extensao_contratacao.agent_main --once
python -m extensao_contratacao.agent_main --loop
```

## Atualizar pelo ACE principal (Push)

1. No CRT → **Automação** → preencha **Pasta remota** (`ctr_agente_dir`), por exemplo:
   `\\PC-CONTRATACAO\ACE_AnalisadorColetaEntrega`
2. Clique **Push agente → outro PC** (ou digite `push ctr`)
3. O ACE copia os `.py` do agente (+ módulos runtime) para o outro PC, grava `version.json` e cria `FORCE_RUN`
4. Se o agente estiver rodando lá (`run_agente.bat`), em até ~5s ele recarrega e roda um ciclo

Comandos equivalentes:

```
/e ctr_agente_dir \\PC-NOME\ACE_AnalisadorColetaEntrega
push ctr
ctr agente status
```

`push` sozinho continua sendo o publish do **site** (GitHub). Para o agente use sempre `push ctr`.


## Relatório 73

Desativado no automático. Use este agente (ou `73` / `ctr excel` no ACE).
Modo legado SSW 073: `73 legado`.
