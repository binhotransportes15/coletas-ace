/**
 * BINHO · OPERACIONAL
 * GitHub Pages hospeda o HTML; dados vêm do Apps Script / planilha única.
 *
 * Mesma URL para Distribuição (50/103/36/225), Armazém (078 + 177) e Pendência (031).
 * Abas 078: Veiculos78 | Resumo78
 * Abas 177: Conferentes177 | Resumo177
 * Abas 031: Pendencias31 | Resumo31 | Ofensores31
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
  pendenciaCsv: {
    resumo: "data/pendencia/resumo_31.csv",
    ofensores: "data/pendencia/ofensores_31.csv",
    pendencias: "data/pendencia/pendencias_31.csv",
  },
  contratacaoCsv: {
    resumo: "data/contratacao/resumo_073.csv",
    veiculos: "data/contratacao/veiculos_073.csv",
    ctrbs: "data/contratacao/ctrbs_073.csv",
    destinos: "data/contratacao/destinos_073.csv",
  },
};
