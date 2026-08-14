# ACE · Analisador Coleta Entrega (BINHO)

Baixa relatórios do SSW (coleta, entrega, armazém, pendência, contratação), analisa e alimenta **cache local**, telas internas e, se quiser, **Google Sheets → Google Sites** (TV) ou GitHub Pages (legado).

## Começar

```bat
pip install -r requirements.txt
playwright install chromium
ace.bat
```

Pacotes principais: **PySide6** (+ Addons/WebEngine), **playwright**, **openpyxl**, **Pillow**, **psutil**. Detalhes em [`requirements.txt`](requirements.txt).

`rodar.bat` também abre o painel CRT.

## Manual

Guia completo (comandos, modo local, paralelo, planilha, pastas):

→ **[docs/MANUAL.md](docs/MANUAL.md)**

Conceito TV via Google Sites (piloto):

→ **[docs/CONCEITO_SITES.md](docs/CONCEITO_SITES.md)**

## Estrutura rápida

| Pasta / arquivo | Conteúdo |
|-----------------|----------|
| `ace.bat` | Entrada principal (CRT) |
| `dashboard/` | Telas TV / SPA (LAN / legado) |
| `data/` | Config, cache, downloads, logs |
| `apps_script/` | Integração planilha |
| `tools/` | Diagnósticos (fora do dia a dia) |
| `docs/` | Manual + conceito Sites |

## Apps Script

Passo a passo da planilha: [`apps_script/README.md`](apps_script/README.md)
