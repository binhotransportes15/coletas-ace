/**
 * BINHO · OPERACIONAL
 * GitHub Pages hospeda o HTML; dados vêm do Apps Script / planilha por setor.
 *
 * Distribuição = coleta + entrega + agendamento (planilha coletas-ace)
 * Armazém = tela 078 (planilha armazem-ace — separada)
 */
window.ACE_CONFIG = {
  scriptUrl:
    "https://script.google.com/macros/s/AKfycbxse6pGIoSJ8CQAZNuRhNsk_xYY-bqY34HtYxAczx3XQz97fjlEIYFISGiGCGHGjUI6/exec",
  armazem: {
    scriptUrl:
      "https://script.google.com/macros/s/AKfycbxaSLfrT7_DKhjZMzBDShU3dFJMQ0vyu6jYYZIF6LZ0oxKaICJF5ywLlPKF-9h-OZfY/exec",
    /** fallback local / Pages se Sheets falhar */
    csvResumo: "data/armazem/resumo_78.csv",
    csvVeiculos: "data/armazem/veiculos_78.csv",
  },
};
