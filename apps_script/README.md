# Apps Script — modelo Vale Pallet

O site no GitHub Pages **só hospeda o HTML**.  
Os dados vêm **ao vivo** da planilha, via Apps Script — **sem token do GitHub no dia a dia**.

## Fluxo

```
ACE (PC) --POST token--> Apps Script --grava--> Google Sheets
Site GitHub Pages --GET--> Apps Script --le--> Google Sheets
```

## 1) Criar / abrir a planilha
Copie o **ID** da URL:

`https://docs.google.com/spreadsheets/d/`**`ID_AQUI`**`/edit`

## 2) Colar o script
1. Planilha → **Extensões → Apps Script**
2. Cole o [`Code.gs`](Code.gs)
3. Preencha:
   ```js
   var SPREADSHEET_ID = 'ID_AQUI';
   var SECRET = 'coletas-ace';
   ```
4. Salvar

## 3) Publicar App da Web
1. **Implantar → Nova implantação** (ou Gerenciar → lápis → **Nova versão**)
2. Tipo: **App da Web**
3. Executar como: **Eu**
4. Quem tem acesso: **Qualquer pessoa**
5. Copie a URL `.../exec`

### Sync sem “zerar” a TV
O `replace` **não apaga mais a aba inteira** antes de gravar: sobrescreve no lugar e limpa só o excedente. Assim, quem abre o Sites/dashboard no meio do ciclo do ACE continua vendo os dados anteriores até os novos terminarem de gravar.

Depois de atualizar o `Code.gs`, publique **Nova versão** da App da Web (só Salvar no editor não basta).

## 4) Configurar o ACE (`data/config.json`)
```json
"enable_sheets": true,
"apps_script_url": "https://script.google.com/macros/s/XXXX/exec",
"apps_script_token": "coletas-ace"
```

## 5) Site (igual Vale Pallet)
Em `dashboard/config.js` já vai a mesma URL do App da Web.  
O site chama:
- `?action=resumo` / `coletas` / `historico` / `coletas103` / …
- `?action=veiculos78` / `resumo78` — Armazém (tela 078)

Abas criadas sob demanda pelo ACE: `Veiculos78`, `Resumo78` (mesma planilha).

**GitHub token só é necessário** se você for alterar o HTML do site e fizer `git push` (igual quando atualiza o Vale Pallet).  
Atualização diária de dados = só o ACE gravando na planilha.

## Teste rapido no navegador
Abra:

`SUA_URL_EXEC?action=ping`

Deve voltar `{"ok":true,...}`.
