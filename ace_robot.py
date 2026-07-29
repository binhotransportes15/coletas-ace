from __future__ import annotations

import sys
import traceback
from datetime import datetime

from config import LOG_DIR, ensure_dirs, load_settings
from pipeline import run_full_pipeline


def main() -> int:
    ensure_dirs()
    log_path = LOG_DIR / f"ace_{datetime.now():%Y%m%d}.log"

    def on_status(msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    on_status("=== ACE robot start ===")
    settings = load_settings()
    try:
        result = run_full_pipeline(
            modo=settings.periodo_modo or "diario",
            settings=settings,
            keep_open=False,
            headless=True,
            on_status=on_status,
        )
        analysis = result.get("analysis") or {}
        on_status(
            f"OK periodo={result.get('period')} lote={analysis.get('lote_atual')} "
            f"historico={analysis.get('historico')}"
        )
        on_status("=== ACE robot end ===")
        return 0
    except Exception as error:  # noqa: BLE001
        on_status(f"FALHA: {error}")
        on_status(traceback.format_exc())
        on_status("Mantendo cache/planilha anteriores.")
        on_status("=== ACE robot end (error) ===")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
