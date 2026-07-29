# Apps Script — ligar a planilha ao ACE

## 1) Criar a planilha
1. Abra https://sheets.google.com e crie uma planilha vazia (ex.: `ACE Coletas`).

## 2) Colar o script
1. Na planilha: **Extensões → Apps Script**
2. Apague o código padrão
3. Cole o conteúdo de [`Code.gs`](Code.gs)
4. Em `const SECRET = 'TROQUE_ESTE_TOKEN'` coloque um token secreto (ex.: `ace-2026-binho`)
5. **Salvar** (Ctrl+S)

## 3) Publicar como App da Web
1. Em Apps Script: **Implantar → Nova implantação**
2. Tipo: **App da Web**
3. Descrição: `ACE bridge`
4. **Executar como:** Eu
5. **Quem tem acesso:** Qualquer pessoa
6. **Implantar**
7. Autorize a conta Google quando pedir
8. **Copie a URL** da implantação (termina com `/exec`)

## 4) Configurar o ACE
Em `data/config.json`:

```json
"enable_sheets": true,
"apps_script_url": "https://script.google.com/macros/s/XXXX/exec",
"apps_script_token": "ace-2026-binho"
```

O `apps_script_token` tem que ser **igual** ao `SECRET` do `Code.gs`.

## 5) Testar
No ACE: **Analisar ultimo relatorio** (com sync ligado).

Devem surgir as abas:
- `Coletas`
- `Historico`
- `ResumoDiario`

## Se alterar o script
Depois de mudar o `Code.gs`, faça **Implantar → Gerenciar implantações → Editar (lápis) → Nova versão → Implantar**.
