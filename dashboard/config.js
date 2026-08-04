/**
 * BINHO · OPERACIONAL
 * GitHub Pages hospeda o HTML; dados vêm do Apps Script / planilha única.
 *
 * Mesma URL para Distribuição (50/103/36/225) e Armazém (078 + 177).
 * Abas 078: Veiculos78 | Resumo78
 * Abas 177: Conferentes177 | Resumo177
 */
window.ACE_CONFIG = {
  scriptUrl:
    "https://script.google.com/macros/s/AKfycbxse6pGIoSJ8CQAZNuRhNsk_xYY-bqY34HtYxAczx3XQz97fjlEIYFISGiGCGHGjUI6/exec",
  /** fallback local / Pages se Sheets falhar */
  armazemCsv: {
    resumo: "data/armazem/resumo_78.csv",
    veiculos: "data/armazem/veiculos_78.csv",
    conferentes: "data/armazem/conferentes_177.csv",
    resumo177: "data/armazem/resumo_177.csv",
  },
};
