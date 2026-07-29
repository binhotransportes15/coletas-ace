# ACE · Analisador Coleta Entrega

Baixa a **opcao 50** (Relacao das Coletas / `ssw0157`), analisa situacoes e historico completo, alimenta CSV local + Google Sheets e publica dashboard (GitHub Pages).

## Periodo (regra D-2)

Cadastro em **D** → rua em **D+1** → relatorio util em **D+2**.

| Modo | Periodo puxado |
|------|----------------|
| **Diario** | `hoje-2` (na segunda: sexta a sabado) |
| **Sexta** | sexta de cadastro (quem sai na segunda) |

## Como usar (UI)

```bat
pip install -r requirements.txt
playwright install chromium
rodar.bat
```

- **Baixar + analisar + enviar** — fluxo completo
- **Analisar ultimo relatorio** — so parser + sync (usa downloads/samples)
- Login: menu **Configuracao → Login SSW** (Ctrl+L)

## Robo no boot do Windows

```bat
instalar_startup.bat
```

Cria atalho em `Startup` que executa `ace_robot.py` (headless, modo diario). Logs em `data/logs/`.

Se SSW/Sheets/GitHub falhar, o cache e a planilha/dashboard **anteriores permanecem**.

## Google Sheets + site (modelo Vale Pallet)

O ACE grava na planilha via Apps Script.  
O site no GitHub Pages **le a planilha ao vivo** — **nao precisa de token GitHub todo dia**.

Passo a passo: [`apps_script/README.md`](apps_script/README.md)

```json
"enable_sheets": true,
"apps_script_url": "https://script.google.com/macros/s/XXXX/exec",
"apps_script_token": "coletas-ace"
```

No `Code.gs` da planilha e obrigatorio:
- `SPREADSHEET_ID` (ID da URL da planilha)
- `SECRET = 'coletas-ace'`
- publicar **Nova versao** da App da Web

Abas: `Coletas`, `Historico`, `ResumoDiario`.

## Dashboard GitHub Pages

Arquivos em `dashboard/` (abra `dashboard/index.html`).

Para publicar:

```json
"enable_github_publish": true,
"github_repo": "usuario/ACE-Dashboard",
"github_branch": "main"
```

Defina a variavel de ambiente `GH_TOKEN` com um PAT que permita push. Ative GitHub Pages na pasta `/dashboard` (ou root, conforme o repo).

## Arquivos principais

- `parser_ssw0157.py` — situaçoes + historico SIT/INSTR
- `pipeline.py` — orquestra download → analise → sheets → dashboard
- `ace_robot.py` — execucao automatica
- `data/cache/*.csv` — fonte local resiliente
