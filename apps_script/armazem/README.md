# Apps Script · ACE Armazém (planilha SEPARADA)

Este bridge **não usa** a planilha da distribuição (`coletas-ace`).

```
ACE Armazém (PC) --POST token armazem-ace--> Apps Script --grava--> Planilha NOVA
Dashboard / TV --GET--> Apps Script --le--> Planilha NOVA
```

## Abas
- `Veiculos78` — linhas da tela 078
- `Resumo78` — totais / status

## Passo a passo
1. Abra https://sheets.new e renomeie para **ACE Armazém 078**
2. Copie o ID da URL: `.../d/ID_AQUI/edit`
3. Planilha → **Extensões → Apps Script** → cole o `Code.gs`
4. Em `Code.gs`:
   ```js
   var SPREADSHEET_ID = 'ID_AQUI';
   var SECRET = 'armazem-ace';
   ```
5. Salvar → **Implantar → Nova implantação → App da Web**
   - Executar como: **Eu**
   - Quem tem acesso: **Qualquer pessoa**
6. Copie a URL `.../exec`
7. No ACE Armazém (`data/config.json` ou bat opção sheets):
   ```json
   "enable_sheets": true,
   "apps_script_url": "https://script.google.com/macros/s/XXXX/exec",
   "apps_script_token": "armazem-ace"
   ```

## Teste
Abra no navegador: `SUA_URL_EXEC?action=ping`  
Deve retornar `"service":"ACE Armazem Sheets Bridge"`.
