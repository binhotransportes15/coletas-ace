# Conta de serviço Google Sheets

1. No Google Cloud Console, crie um projeto e ative **Google Sheets API** + **Google Drive API**.
2. Crie uma **Service Account** e baixe o JSON.
3. Salve o arquivo como:

`data/secrets/google_service_account.json`

4. Abra a planilha Google e compartilhe com o e-mail da service account (permissão Editor).
5. Em `data/config.json`:

```json
"enable_sheets": true,
"google_sheet_id": "ID_DA_PLANILHA_NA_URL"
```

Sem esse arquivo, o ACE continua funcionando com CSV local e não quebra o robô.
