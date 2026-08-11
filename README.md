# ACE · Analisador Coleta Entrega (BINHO)

Baixa relatórios do SSW (coleta, entrega, armazém, pendência, contratação), analisa e alimenta **cache local**, telas internas e, se quiser, Google Sheets + GitHub Pages.

## Começar

```bat
pip install -r requirements.txt
playwright install chromium
ace.bat
```

`rodar.bat` também abre o painel CRT.

## Manual

Guia completo (comandos, modo local, paralelo, planilha, pastas):

→ **[docs/MANUAL.md](docs/MANUAL.md)**

## Estrutura rápida

| Pasta / arquivo | Conteúdo |
|-----------------|----------|
| `ace.bat` | Entrada principal (CRT) |
| `dashboard/` | Telas TV / SPA |
| `data/` | Config, cache, downloads, logs |
| `apps_script/` | Integração planilha |
| `tools/` | Diagnósticos (fora do dia a dia) |
| `docs/` | Manual |

## Apps Script

Passo a passo da planilha: [`apps_script/README.md`](apps_script/README.md)
