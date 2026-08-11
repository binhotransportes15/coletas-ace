# Manual operacional · ACE (BINHO)

Analisador de Coleta e Entrega: baixa relatórios do SSW, analisa, alimenta cache local (CSV/JSON) e, se configurado, planilha + dashboard.

Entrada principal: **`ace.bat`** (painel CRT).

---

## 1. Instalação

```bat
pip install -r requirements.txt
playwright install chromium
ace.bat
```

Credenciais e opções ficam em `data/config.json` (aba **Configuração** do CRT).

---

## 2. Painel CRT

Abas à direita:

| Aba | Uso |
|-----|-----|
| **Configuração** | Login SSW, intervalo, Sheets, flags do loop |
| **Local** | Telas internas sem GitHub/Sheets; JSON em `data/cache/local/` |
| **TV** | Layout da parede / slots |
| **Gestão** | Armazém, pendência, contratação, publicar, atualização contínua |

Centro: atalhos (Coletas, Limites, Entregas…) + histórico CMD + prompt.

Atalhos úteis no prompt: `local`, `automatica`, `50`, `31`, `73`, `parar`, `help`.

---

## 3. Comandos do dia

| Comando | O que faz |
|---------|-----------|
| `50` | Coletas (opção 50) |
| `103` | Situação de coletas / torres |
| `36` | Entregas (romaneios) |
| `225` | Agendamentos do mês |
| `78` | Armazém (pátio) + tenta 177 |
| `177` | Só conferentes |
| `31` | Pendência (ocorrências / SLA) |
| `73` | Contratação 073 → filiais 076+200 |
| `sync` | Envia distribuição à planilha (se não estiver em modo local) |
| `dash` | Atualiza arquivos do dashboard local |
| `local` | Abre telas internas (veja §4) |
| `/automatica` ou `automatica` | Loop contínuo |
| `/push` | Publica no GitHub Pages |
| `/viz on\|off` | Mostra/oculta o navegador Playwright |
| `gui` | GUI antiga (`app.py`) — legado |
| `help` | Lista campos e comandos |

Exemplos:

```text
local coleta pendencia
local
31 63 60
73 so73
automatica 5m
/e modo_local true
/e ciclo_paralelo true
```

---

## 4. Modo local (sem planilha / sem GitHub)

Com **`modo_local=true`** (CRT → Local → “Não enviar à planilha…”, ou `/e modo_local true`):

- Relatórios **não** vão ao Google Sheets
- **Não** há push automático para o GitHub
- Dados ficam em:
  - `data/cache/*.csv` — cache operacional
  - `data/cache/local/*.json` — snapshot rápido por setor
  - `dashboard/data/` — o que as telas leem

Na aba **Local**, marque as telas e use **Abrir selecionadas** (várias janelas ao mesmo tempo).

Telas: `coleta` · `entrega` · `agendamento` · `armazem` · `conferentes` · `pendencia` · `contratacao`

Para voltar à nuvem: `/e modo_local false` e `enable_sheets true` (se for usar planilha).

### Acesso na mesma rede (celular / outra TV)

Não precisa de um IP por setor. O PC do ACE tem **um IP** na Wi‑Fi; cada setor é um link diferente:

```text
http://IP-DO-PC:8787/index.html#tv/distribuicao/coleta
http://IP-DO-PC:8787/index.html#tv/armazem
http://IP-DO-PC:8787/index.html#tv/pendencia
…
```

1. CRT → aba **Local** → marque **Liberar acesso na rede**
2. Ou digite `lan` no prompt / `/e dashboard_lan true`
3. Clique **Mostrar links da rede** (ou comando `lan`) e abra o link no outro aparelho (mesma Wi‑Fi)
4. Porta padrão: `8787` (`/e dashboard_port 8787`)
5. Na 1ª vez o Windows Firewall pode pedir permissão — aceite

O CRT precisa ficar aberto (o servidor roda nele).

---

## 5. Ciclo paralelo (`ciclo_paralelo`)

No `/automatica`, com paralelo ligado (padrão):

- **dist** (50+103+36+225) roda em um browser
- **078**, **031** e **073** rodam **ao mesmo tempo** (browsers separados)

Desligar: `/e ciclo_paralelo false` (volta à sequência dist → 078 → 031 → 073).

Flags do loop:

- `armazem_in_loop` · `pendencia_in_loop` · `contratacao_in_loop`
- `loop_intervalo` — ex.: `30s`, `5m`, `1h`

---

## 6. Planilha e GitHub Pages (modo nuvem)

Só quando **`modo_local=false`**.

Planilha (Apps Script):

```json
"enable_sheets": true,
"apps_script_url": "https://script.google.com/macros/s/XXXX/exec",
"apps_script_token": "seu-token"
```

Detalhes: [`apps_script/README.md`](../apps_script/README.md)

Pages:

```json
"enable_github_publish": true,
"github_repo": "owner/repo",
"github_branch": "main"
```

Variável de ambiente `GH_TOKEN` (ou o nome em `github_token_env`). Publicar: `/push`.

Dashboard versionado em `dashboard/`. Redirect da raiz do Pages: `index.html` → `./dashboard/`.

---

## 7. Pastas importantes

```text
data/
  config.json          # login e settings (não versionar)
  cache/               # CSV de trabalho
  cache/local/         # JSON do modo local
  downloads/           # arquivos SSW baixados
  logs/                # logs diários
  secrets/             # service account etc.
dashboard/             # SPA TV + data/
apps_script/           # scripts da planilha
tools/                 # diagnósticos (não usar no dia a dia)
docs/MANUAL.md         # este arquivo
ace.bat                # abre o CRT
rodar.bat              # atalho para ace.bat
abrir_dashboard.bat    # abre dashboard\index.html no browser
```

---

## 8. Boot no Windows (legado)

```bat
instalar_startup.bat
```

Cria atalho em Startup que roda `ace_robot.py` (headless).  
Fluxo recomendado no dia a dia: abrir **`ace.bat`** e usar **Atualização contínua** / `automatica` no CRT.

---

## 9. Problemas comuns

| Sintoma | O que checar |
|---------|----------------|
| Login SSW falha | Usuário/senha/unidade na Configuração; rede |
| Timeout fila 156 | Job já sumiu ou SSW lento; rode de novo o relatório |
| Planilha não atualiza | `modo_local` ligado? `enable_sheets`? URL/token Apps Script? |
| Telas locais vazias | Rode o relatório (ex. `50`) e **Atualizar dados (local)** |
| WebEngine não abre | PySide6 WebEngine; use “No navegador” na janela local |
| Chromium Playwright | `playwright install chromium` |

Diagnósticos avançados: pasta [`tools/`](../tools/README.md).

---

## 10. Atalhos de arquivo

| Arquivo | Função |
|---------|--------|
| `ace.bat` | CRT (padrão) |
| `ace.bat cmd` | Console texto |
| `ace.bat automatica` | Loop sem abrir o menu |
| `rodar.bat` | Igual `ace.bat` |
| `abrir_dashboard.bat` | Abre o HTML do dashboard |
