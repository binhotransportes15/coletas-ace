# Conceito · ACE TV via Google Sites

Objetivo: a **parede (TV)** deixa de depender do GitHub Pages e passa a consumir um **Google Sites** alimentado pela **planilha** (Apps Script). O ACE continua sendo o motor (SSW → CSV).

```text
SSW ──► ACE (parsers) ──► CSV local
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         Google Sheets    LAN dashboard   GitHub Pages
         (Apps Script)    (opcional)      (legado)
              │
              ▼
         Google Sites  ←── TV da parede
```

---

## 1. Papéis

| Camada | Papel |
|--------|--------|
| **ACE** (CRT / Playwright / parsers) | Baixa SSW, analisa, grava `data/cache/*.csv` |
| **Google Sheets** | Fonte na nuvem; sync via `sync` / pipelines (`sheets_sync*.py`) |
| **Google Sites** | Casca da TV: embeds de abas/gráficos da planilha |
| **Dashboard local / LAN** | Debug e rede interna (`modo_local`, porta 8787) |
| **GitHub Pages (`/push`)** | Legado — manter código, desligar no dia a dia do piloto |

**Regra do piloto:** TV = Sites · PC ACE = CRT (+ LAN se quiser) · sem `/push` obrigatório.

---

## 2. Por que Sites (e não hospedar o `index.html`)

- Sites **não** substitui o SPA atual (`dashboard/index.html` + CSV + slots TV).
- O ganho de velocidade vem do **POST Apps Script → Sheets** (segundos), não do `git push` + build Pages (minutos).
- Sites embute tabelas/gráficos da planilha; a TV só precisa abrir o link e atualizar (F5 ou auto-refresh do navegador).

---

## 3. Destino de publicação (`publish_target`)

Campo em `data/config.json` / `/e publish_target …`:

| Valor | Comportamento |
|-------|----------------|
| `sites` | Prefere planilha; **não** faz push GitHub |
| `github` | Fluxo Pages (se `enable_github_publish`) |
| `local` | Só CSV/dashboard local (sem push) |
| `auto` | Infere: `modo_local` → local; senão se GitHub ligado → github; senão se Sheets ligado → **sites**; senão local |

Piloto recomendado:

```text
/e modo_local false
/e enable_sheets true
/e enable_github_publish false
/e publish_target sites
/e google_sites_url https://sites.google.com/view/SEU-SITE/...
```

Atalhos no prompt: `sites` (abre o link) · `piloto_sites` (aplica flags do piloto).

---

## 4. Inventário Sheets (o que já sobe)

Mesma planilha / Apps Script (`apps_script_url` + token). Contratação **ainda não** tem `sheets_sync` (só CSV local / dashboard).

### Distribuição — piloto

| Aba | Origem | Sync | Uso no Sites |
|-----|--------|------|--------------|
| `Coletas` | 50 | `sync` / ciclo | detalhe (pesado para embed) |
| `Historico` | 50 | ciclo | eventos |
| `ResumoDiario` | 50 | ciclo | **KPI do dia** (bom embed) |
| `Coletas103` | 103 | ciclo | torres / lista |
| `Resumo103` | 103 | ciclo | **KPI torres** (parado / rota / …) |
| `Entregas36` / `Romaneios36` / `Resumo36` | 36 | ciclo | entregas |
| `Agendamentos225` / `Alertas225` / `Resumo225` | 225 | ciclo | agenda |

Colunas úteis no piloto (Coleta):

- `ResumoDiario`: `data_cadastro`, `total_coletas`, `cadastrada`, `comandada`, `coletada`, `cancelada`
- `Resumo103`: `periodo`, `total`, `parado`, `em_rota`, `realizada`, `cancelada`, `outro`

### Armazém

| Aba | Sync |
|-----|------|
| `Veiculos78`, `Resumo78` | `sheets_sync_78` (pipeline 78) |
| `Conferentes177`, `Resumo177` | idem (177) |

`Resumo78`: `atualizado`, `total_veiculos`, `peso_total`, `finalizado`, `descarregando`, `atrasado`, …

### Pendência

| Aba | Sync |
|-----|------|
| `Pendencias31`, `Resumo31`, `Ofensores31` | `sheets_sync_31` |

`Resumo31`: `periodo`, `atualizado`, `total_ctrcs`, `sla_pct`, `abertas`, …

### Contratação / Emissão

| Setor | Sheets hoje |
|-------|-------------|
| Contratação (073) | **não sync** — só `dashboard/data/contratacao/` |
| Emissão (455) | **ainda não existe** no ACE |

Próximas fases: sync TV-ready + páginas Sites por setor.

---

## 5. Setores na parede (visão)

| Sites (página) | Conteúdo sugerido | Fonte Sheets |
|----------------|-------------------|--------------|
| Coleta (piloto) | KPIs + tabela resumo 103 | `ResumoDiario`, `Resumo103` |
| Entrega | `Resumo36` | 36 |
| Agendamento | `Resumo225` / alertas | 225 |
| Armazém | `Resumo78` | 78 |
| Pendência | `Resumo31` + ofensores | 31 |
| Contratação | TBD após sync 073 | — |
| Emissão | TBD após 455 | — |

---

## 6. Piloto — montar e medir

### 6.1 No Google (manual)

1. Abra a planilha já usada pelo ACE (`google_sheet_id` / Apps Script).
2. Confirme abas `ResumoDiario` e `Resumo103` (rode `103` + `sync` se vazias).
3. Crie um **Google Sites** (ex.: “ACE TV — piloto”).
4. Página **Coleta**:
   - Inserir → **Planilhas** (ou gráfico) embutindo `Resumo103` e/ou `ResumoDiario`.
   - Título grande + data/hora se quiser (célula `atualizado` / `periodo`).
5. Publique o Sites e copie a URL pública → `/e google_sites_url …`.

### 6.2 No ACE

```text
piloto_sites
sites
103
sync
```

(Ou `automatica` com Sheets ligado.)

### 6.3 Medir latência

Anote no papel / planilha de teste:

| Evento | Hora |
|--------|------|
| Fim do `sync` no CRT (mensagem Sheets OK) | |
| Número novo visível no Sites (após F5) | |
| (opcional) `/push` Pages até TV atualizar | |

**Sucesso do piloto:** Sites atualiza **claramente mais rápido** que Pages e o layout é legível na TV.

### 6.4 Se o embed não atualizar

- No Sites, o iframe da planilha às vezes cacheia: F5 forçado ou reabrir a aba.
- Confirme que o Apps Script publicou **Nova versão** e `enable_sheets true`.
- Em `modo_local true` a planilha **não** recebe dados.

---

## 7. Fase 2 (depois do piloto)

- Uma página Sites por setor.
- Abas “TV-ready” (só KPIs) se o embed bruto for feio.
- CRT: botão/atalho `sites` (já previsto).
- Desligar `enable_github_publish` no operacional.
- Emissão 455 e sync da Contratação entram **depois**, já no trilho Sheets → Sites.

---

## 8. O que este conceito **não** faz

- Não reescreve o `dashboard/index.html` dentro do Sites.
- Não apaga GitHub Pages nem o servidor LAN.
- Não implementa SSW 455 nesta fase.
