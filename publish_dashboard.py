from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DASHBOARD_DIR, BASE_DIR, AceSettings, github_publish_allowed, load_settings, resolve_publish_target
from parser_ssw0157 import COLETAS_CSV, RESUMO_CSV, HISTORICO_CSV
from parser_ssw103 import COLETAS_103_CSV, RESUMO_103_CSV
from parser_ssw0146 import ENTREGAS_36_CSV, ROMANEIOS_36_CSV, RESUMO_36_CSV
from parser_ssw225 import AGENDAMENTOS_225_CSV, RESUMO_225_CSV, ALERTAS_225_CSV
from parser_ssw78 import RESUMO_CSV as RESUMO_78_CSV, VEICULOS_CSV as VEICULOS_78_CSV
from parser_ssw177 import CONFERENTES_CSV as CONF_177_CSV, RESUMO_177_CSV
from parser_ssw31 import (
    OFENSORES_31_CSV,
    PENDENCIAS_31_CSV,
    RESUMO_31_CSV,
)
from parser_ssw455 import (
    DIAS_MES_455_CSV,
    EXPEDIDORES_455_CSV,
    EXPEDIDORES_MES_455_CSV,
    HORAS_455_CSV,
    RESUMO_455_CSV,
    RESUMO_MES_455_CSV,
)
from mapa_distribuicao import publish_mapa_local

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _safe_write_text(path: Path, text: str, *, encoding: str = "utf-8", retries: int = 5) -> None:
    """Grava texto com arquivo temporário + replace (evita Errno 22 / lock no Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    last: BaseException | None = None
    for attempt in range(max(1, retries)):
        try:
            tmp.write_text(text, encoding=encoding)
            os.replace(str(tmp), str(path))
            return
        except OSError as err:
            last = err
            try:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)  # type: ignore[call-arg]
            except TypeError:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
            except OSError:
                pass
            time.sleep(0.05 * (attempt + 1))
            try:
                # fallback direto (alguns AV bloqueiam só o replace)
                path.write_text(text, encoding=encoding)
                return
            except OSError as err2:
                last = err2
                time.sleep(0.08 * (attempt + 1))
    if last:
        raise last


def _copy_file(src: Path, dest: Path) -> None:
    """Copia arquivo; se copy2 falhar em utime (Errno 22), usa copyfile."""
    try:
        shutil.copy2(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def _copy_cache_to_dashboard() -> dict[str, str]:
    data_dir = DASHBOARD_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for src, name in (
        (COLETAS_CSV, "coletas.csv"),
        (HISTORICO_CSV, "historico.csv"),
        (RESUMO_CSV, "resumo_diario.csv"),
        (COLETAS_103_CSV, "coletas_103.csv"),
        (RESUMO_103_CSV, "resumo_103.csv"),
        (ENTREGAS_36_CSV, "entregas_36.csv"),
        (ROMANEIOS_36_CSV, "romaneios_36.csv"),
        (RESUMO_36_CSV, "resumo_36.csv"),
        (AGENDAMENTOS_225_CSV, "agendamentos_225.csv"),
        (RESUMO_225_CSV, "resumo_225.csv"),
        (ALERTAS_225_CSV, "alertas_225.csv"),
    ):
        dest = data_dir / name
        if src.exists():
            _copy_file(src, dest)
            paths[name] = str(dest)
        elif not dest.exists():
            with dest.open("w", encoding="utf-8-sig", newline="") as fh:
                if name == "resumo_diario.csv":
                    fh.write("data_cadastro,total_coletas,cadastrada,comandada,coletada,cancelada\n")
                elif name == "resumo_103.csv":
                    fh.write("periodo,total,parado,em_rota,realizada,cancelada,outro\n")
                elif name == "coletas_103.csv":
                    fh.write(
                        "coleta_id,situacao_atual,status_ace,hora,hora_antes_meio_dia,"
                        "cadastrada_ref_AI,placa,placa_carreta,motorista\n"
                    )
                elif name == "entregas_36.csv":
                    fh.write(
                        "ctrc_id,romaneio,situacao,status_ace,placa,placa_carreta,"
                        "motorista,destinatario,ocorrencia,data_ocorrencia,excluido,motivo_exclusao\n"
                    )
                elif name == "romaneios_36.csv":
                    fh.write(
                        "romaneio,placa,placa_carreta,motorista,total,realizada,em_rota,pendencia,pct\n"
                    )
                elif name == "resumo_36.csv":
                    fh.write("periodo,total,realizada,em_rota,pendencia,excluido\n")
                elif name == "agendamentos_225.csv":
                    fh.write(
                        "ctrc,remetente,destinatario,destino,peso,frete,"
                        "agendado_em,agendado_para,agendado_para_data,status_raw,status_ace,alerta_sem_saida\n"
                    )
                elif name == "resumo_225.csv":
                    fh.write("periodo,total,em_rota,parado,concluido,alerta\n")
                elif name == "alertas_225.csv":
                    fh.write("ctrc,destinatario,destino,agendado_para,status_raw\n")
                elif name == "historico.csv":
                    fh.write(
                        "event_key,coleta_id,coleta,seq_evento,dominio,unidade_evento,"
                        "usuario,data,hora,observacao\n"
                    )
                else:
                    fh.write("coleta_id,coleta,situacao_atual\n")
            paths[name] = str(dest)
    meta = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "files": paths,
    }
    _safe_write_text(
        data_dir / "meta.json",
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    for copier in (_copy_armazem_to_dashboard, _copy_pendencia_to_dashboard, _copy_emissao_to_dashboard):
        try:
            paths.update(copier())
        except Exception:
            # Setor auxiliar não pode derrubar o publish inteiro (ex.: stamp com lock)
            continue
    return paths


def _copy_pendencia_to_dashboard() -> dict[str, str]:
    """Copia CSVs 031 para dashboard/data/pendencia/."""
    data_dir = DASHBOARD_DIR / "data" / "pendencia"
    data_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    defaults = {
        "pendencias_31.csv": (
            "ctrc,data_emissao,ultima_ocorrencia,historico,codigo,codigo_consulta,"
            "descricao_ocorrencia,complemento_ocorrencia,descricao_codigo\n"
        ),
        "resumo_31.csv": (
            "periodo,atualizado,total_ctrcs,total_codigos,solucionadas,abertas,sla_pct,"
            "sla_medio_dias,valor_risco,aging_0_2,aging_3_5,aging_6_mais,"
            "topo_codigo,topo_label,topo_qtd\n"
        ),
        "ofensores_31.csv": "codigo,label,qtd,pct,polaridade\n",
    }
    for src, name in (
        (PENDENCIAS_31_CSV, "pendencias_31.csv"),
        (RESUMO_31_CSV, "resumo_31.csv"),
        (OFENSORES_31_CSV, "ofensores_31.csv"),
    ):
        dest = data_dir / name
        if src.exists():
            _copy_file(src, dest)
            out[f"pendencia/{name}"] = str(dest)
        elif not dest.exists():
            _safe_write_text(dest, defaults[name], encoding="utf-8-sig")
            out[f"pendencia/{name}"] = str(dest)
    return out


def publish_pendencia_local(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    status = on_status or _noop
    paths = _copy_pendencia_to_dashboard()
    data_dir = DASHBOARD_DIR / "data" / "pendencia"
    stamp = {
        "ts": datetime.now().timestamp(),
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    stamp_path = data_dir / "stamp.json"
    try:
        _safe_write_text(stamp_path, json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
        paths["pendencia/stamp.json"] = str(stamp_path)
    except Exception:
        pass
    # Bump meta.json para a TV/LAN detectar mudança (igual emissão)
    try:
        meta_path = DASHBOARD_DIR / "data" / "meta.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
        files.update(paths)
        meta["files"] = files
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _safe_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["meta.json"] = str(meta_path)
    except Exception:
        pass
    status(f"Dashboard Pendência local: {len(paths)} arquivo(s).")
    return {"ok": True, "local": paths}


def _copy_emissao_to_dashboard() -> dict[str, str]:
    """Copia CSVs 455 para dashboard/data/emissao/ + stamp para a TV detectar mudança."""
    data_dir = DASHBOARD_DIR / "data" / "emissao"
    data_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    defaults = {
        "resumo_455.csv": (
            "periodo,mes,atualizado,ctes,peso,peso_fmt,valor_mercadoria,valor_mercadoria_fmt,"
            "volumes,cubagem,cubagem_fmt,frete,frete_fmt,dia,noite,cancelados,pendentes,finalizados\n"
        ),
        "expedidores_455.csv": "nome,nome_exibicao,qtd,pct\n",
        "horas_455.csv": "hora,label,qtd\n",
    }
    for src, name in (
        (RESUMO_455_CSV, "resumo_455.csv"),
        (EXPEDIDORES_455_CSV, "expedidores_455.csv"),
        (HORAS_455_CSV, "horas_455.csv"),
    ):
        dest = data_dir / name
        if src.exists():
            _copy_file(src, dest)
            out[f"emissao/{name}"] = str(dest)
        elif not dest.exists():
            _safe_write_text(dest, defaults[name], encoding="utf-8-sig")
            out[f"emissao/{name}"] = str(dest)

    atualizado = ""
    resumo_dest = data_dir / "resumo_455.csv"
    if resumo_dest.exists():
        try:
            with resumo_dest.open(encoding="utf-8-sig", newline="") as fh:
                row = next(csv.DictReader(fh), {}) or {}
                atualizado = str(row.get("atualizado") or "")
        except Exception:
            atualizado = ""
    stamp = {
        "ts": datetime.now().timestamp(),
        "atualizado": atualizado or datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    stamp_path = data_dir / "stamp.json"
    try:
        _safe_write_text(stamp_path, json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
        out["emissao/stamp.json"] = str(stamp_path)
    except OSError:
        pass

    try:
        meta_path = DASHBOARD_DIR / "data" / "meta.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
        files.update(out)
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        meta["files"] = files
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        _safe_write_text(
            meta_path,
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        out["meta.json"] = str(meta_path)
    except Exception:
        pass
    return out


def _copy_emissao_mes_to_dashboard() -> dict[str, str]:
    """Copia CSVs 455 mês → dashboard/data/emissao_mes/ (separado da diária)."""
    data_dir = DASHBOARD_DIR / "data" / "emissao_mes"
    data_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    defaults = {
        "resumo_mes_455.csv": (
            "periodo,mes,atualizado,ctes,peso,peso_fmt,valor_mercadoria,valor_mercadoria_fmt,"
            "volumes,cubagem,cubagem_fmt,frete,frete_fmt,dia,noite,cancelados,pendentes,finalizados\n"
        ),
        "expedidores_mes_455.csv": "nome,nome_exibicao,qtd,pct\n",
        "dias_mes_455.csv": "dia,label,qtd\n",
    }
    for src, name in (
        (RESUMO_MES_455_CSV, "resumo_mes_455.csv"),
        (EXPEDIDORES_MES_455_CSV, "expedidores_mes_455.csv"),
        (DIAS_MES_455_CSV, "dias_mes_455.csv"),
    ):
        dest = data_dir / name
        if src.exists():
            _copy_file(src, dest)
            out[f"emissao_mes/{name}"] = str(dest)
        elif not dest.exists():
            _safe_write_text(dest, defaults[name], encoding="utf-8-sig")
            out[f"emissao_mes/{name}"] = str(dest)

    atualizado = ""
    resumo_dest = data_dir / "resumo_mes_455.csv"
    if resumo_dest.exists():
        try:
            with resumo_dest.open(encoding="utf-8-sig", newline="") as fh:
                row = next(csv.DictReader(fh), {}) or {}
                atualizado = str(row.get("atualizado") or "")
        except Exception:
            atualizado = ""
    stamp = {
        "ts": datetime.now().timestamp(),
        "atualizado": atualizado or datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "modo": "mes",
    }
    stamp_path = data_dir / "stamp.json"
    try:
        _safe_write_text(stamp_path, json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
        out["emissao_mes/stamp.json"] = str(stamp_path)
    except OSError:
        pass

    try:
        meta_path = DASHBOARD_DIR / "data" / "meta.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
        files.update(out)
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        meta["files"] = files
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        _safe_write_text(
            meta_path,
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        out["meta.json"] = str(meta_path)
    except Exception:
        pass
    return out


def publish_emissao_local(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    status = on_status or _noop
    paths = _copy_emissao_to_dashboard()
    status(f"Dashboard Emissão local: {len(paths)} arquivo(s).")
    return {"ok": True, "local": paths}


def publish_emissao_mes_local(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    status = on_status or _noop
    paths = _copy_emissao_mes_to_dashboard()
    status(f"Dashboard Emissão Mês local: {len(paths)} arquivo(s).")
    return {"ok": True, "local": paths, "modo": "mes"}


def _copy_reciclagem_to_dashboard() -> dict[str, str]:
    """Espelha JSON/CSVs de reciclagem no dashboard (TV local)."""
    from local_store import persist_sector
    from parser_ssw019 import POR_FILIAL_019_CSV, RESUMO_019_CSV, TOP_CTE_019_CSV
    from parser_ssw081 import RESUMO_081_CSV, TOP_CLIENTE_081_CSV, TOP_CTE_081_CSV

    data_dir = DASHBOARD_DIR / "data" / "reciclagem"
    data_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    # garante JSON atualizado
    try:
        snap = persist_sector("reciclagem")
        if snap.get("path"):
            out["local/reciclagem.json"] = str(snap["path"])
    except Exception:  # noqa: BLE001
        pass
    for src, name in (
        (RESUMO_019_CSV, "resumo_019.csv"),
        (TOP_CTE_019_CSV, "top_cte_019.csv"),
        (POR_FILIAL_019_CSV, "por_filial_019.csv"),
        (RESUMO_081_CSV, "resumo_081.csv"),
        (TOP_CTE_081_CSV, "top_cte_081.csv"),
        (TOP_CLIENTE_081_CSV, "top_cliente_081.csv"),
    ):
        dest = data_dir / name
        if src.exists():
            _copy_file(src, dest)
            out[f"reciclagem/{name}"] = str(dest)
    # espelho do JSON também em data/reciclagem/
    local_json = DASHBOARD_DIR / "data" / "local" / "reciclagem.json"
    if local_json.is_file():
        dest = data_dir / "reciclagem.json"
        try:
            _copy_file(local_json, dest)
            out["reciclagem/reciclagem.json"] = str(dest)
        except Exception:  # noqa: BLE001
            pass
    return out


def publish_reciclagem_local(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    status = on_status or _noop
    paths = _copy_reciclagem_to_dashboard()
    status(f"Dashboard Reciclagem local: {len(paths)} arquivo(s).")
    return {"ok": True, "local": paths}


def _copy_armazem_to_dashboard() -> dict[str, str]:
    """Copia CSVs 078 para dashboard/data/armazem/ (fallback local do hub)."""
    data_dir = DASHBOARD_DIR / "data" / "armazem"
    data_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for src, name in (
        (VEICULOS_78_CSV, "veiculos_78.csv"),
        (RESUMO_78_CSV, "resumo_78.csv"),
        (CONF_177_CSV, "conferentes_177.csv"),
        (RESUMO_177_CSV, "resumo_177.csv"),
    ):
        dest = data_dir / name
        if src.exists():
            _copy_file(src, dest)
            out[f"armazem/{name}"] = str(dest)
        elif not dest.exists():
            if name == "resumo_78.csv":
                _safe_write_text(
                    dest,
                    "atualizado,total_linhas,total_veiculos,peso_total,"
                    "finalizado,descarregando,atrasado,aguardando,chegou\n",
                    encoding="utf-8-sig",
                )
            elif name == "resumo_177.csv":
                _safe_write_text(
                    dest,
                    "atualizado,mes,total_conferentes,peso_total,peso_total_fmt,"
                    "vol_total,vol_total_fmt,topo,topo_peso,topo_vol\n",
                    encoding="utf-8-sig",
                )
            elif name == "conferentes_177.csv":
                _safe_write_text(
                    dest,
                    "rank,login,conferente,nome,unidade,peso_lidos,peso_lidos_fmt,vol_lidos,pct,mes\n",
                    encoding="utf-8-sig",
                )
            else:
                _safe_write_text(
                    dest,
                    "origem,origem_sigla,cavalo,carreta,manifesto,peso,peso_num,saida,prev_chegada,"
                    "chegada,inicio_descarga,final_descarga,status,atrasado,"
                    "tempo_descarga_min,tempo_descarga,peso_veiculo\n",
                    encoding="utf-8-sig",
                )
            out[f"armazem/{name}"] = str(dest)
    return out


def publish_armazem_local(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    status = on_status or _noop
    paths = _copy_armazem_to_dashboard()
    status(f"Dashboard Armazém local: {len(paths)} arquivo(s).")
    return {"ok": True, "local": paths}


def _copy_contratacao_to_dashboard() -> dict[str, str]:
    from parser_ssw073 import CTRBS_073_CSV, DESTINOS_073_CSV, RESUMO_073_CSV, VEICULOS_073_CSV

    data_dir = DASHBOARD_DIR / "data" / "contratacao"
    data_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for src, name, empty in (
        (
            VEICULOS_073_CSV,
            "veiculos_073.csv",
            "placa,carreta,propriedade,grupo,qtd_ctrb,custo,custo_av,valor_pagar,peso,frete,ctrbs\n",
        ),
        (
            RESUMO_073_CSV,
            "resumo_073.csv",
            "periodo,atualizado,unidade,total_veiculos,total_ctrbs,custo,custo_fmt,"
            "frete,frete_fmt,peso,peso_fmt,agregado,frota,contratados,terceiro\n",
        ),
        (
            CTRBS_073_CSV,
            "ctrbs_073.csv",
            "ctrb,tipo,situacao,placa,carreta,propriedade,grupo,custo,custo_av,"
            "valor_pagar,total_ctrb,peso,frete,origem,destino,cidade_destino,fonte\n",
        ),
        (
            DESTINOS_073_CSV,
            "destinos_073.csv",
            "destino,qtd,custo,frete,peso\n",
        ),
    ):
        dest = data_dir / name
        if src.exists():
            _copy_file(src, dest)
            out[f"contratacao/{name}"] = str(dest)
        elif not dest.exists():
            _safe_write_text(dest, empty, encoding="utf-8-sig")
            out[f"contratacao/{name}"] = str(dest)

    # stamp local — painel Contratação não depende da version Sheets
    atualizado = ""
    resumo_dest = data_dir / "resumo_073.csv"
    if resumo_dest.exists():
        try:
            with resumo_dest.open(encoding="utf-8-sig", newline="") as fh:
                row = next(csv.DictReader(fh), {}) or {}
                atualizado = str(row.get("atualizado") or "")
        except Exception:
            atualizado = ""
    stamp = {
        "ts": datetime.now().timestamp(),
        "atualizado": atualizado or datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    stamp_path = data_dir / "stamp.json"
    try:
        _safe_write_text(stamp_path, json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
        out["contratacao/stamp.json"] = str(stamp_path)
    except OSError:
        pass
    return out


def publish_contratacao_local(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    status = on_status or _noop
    paths = _copy_contratacao_to_dashboard()
    status(f"Dashboard Contratação local: {len(paths)} arquivo(s).")
    return {"ok": True, "local": paths}


def ensure_dashboard_files() -> None:
    """Garante HTML/JS do dashboard e CSVs locais (nao sobrescreve index real)."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data").mkdir(parents=True, exist_ok=True)
    index = DASHBOARD_DIR / "index.html"
    # Nunca sobrescrever o dashboard BINHO com o HTML legado embutido.
    if not index.exists():
        index.write_text(_DASHBOARD_HTML, encoding="utf-8")
    _copy_cache_to_dashboard()


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ACE · Coletas</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root { color-scheme: dark; font-family: Segoe UI, system-ui, sans-serif; }
    body { margin: 0; background: #0b1220; color: #e2e8f0; }
    header { padding: 24px 28px 8px; }
    h1 { margin: 0 0 6px; font-size: 28px; }
    .sub { color: #94a3b8; margin: 0; }
    .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; padding: 16px 28px 28px; }
    .card { background: #020617; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; }
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 0 28px 8px; }
    .kpi { background: #020617; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; }
    .kpi b { display: block; font-size: 22px; margin-top: 4px; }
    .muted { color: #94a3b8; font-size: 12px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid #1e293b; padding: 8px; text-align: left; }
    th { color: #67e8f9; }
    @media (max-width: 900px) {
      .grid, .kpis { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>ACE · Analisador Coleta</h1>
    <p class="sub" id="updated">Carregando dados...</p>
  </header>
  <section class="kpis">
    <div class="kpi"><span class="muted">Total</span><b id="kTotal">0</b></div>
    <div class="kpi"><span class="muted">Coletadas</span><b id="kColetada">0</b></div>
    <div class="kpi"><span class="muted">Comandadas</span><b id="kComandada">0</b></div>
    <div class="kpi"><span class="muted">Canceladas</span><b id="kCancelada">0</b></div>
  </section>
  <section class="grid">
    <div class="card">
      <h3>Situações por dia de cadastro</h3>
      <canvas id="chartResumo" height="140"></canvas>
    </div>
    <div class="card">
      <h3>Distribuição atual</h3>
      <canvas id="chartPizza" height="140"></canvas>
    </div>
    <div class="card" style="grid-column: 1 / -1;">
      <h3>Últimas coletas</h3>
      <div style="overflow:auto; max-height:320px;">
        <table>
          <thead>
            <tr>
              <th>Coleta</th><th>Situação</th><th>Cadastrada</th><th>Comandada</th>
              <th>Coletada</th><th>Motorista</th><th>Destino</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>
  </section>
  <script>
    async function loadCsv(path) {
      const res = await fetch(path + '?t=' + Date.now());
      const text = await res.text();
      const lines = text.trim().split(/\\r?\\n/);
      if (lines.length < 2) return [];
      const headers = lines[0].split(',');
      return lines.slice(1).map(line => {
        const cols = [];
        let cur = '', q = false;
        for (let i = 0; i < line.length; i++) {
          const ch = line[i];
          if (ch === '"') { q = !q; continue; }
          if (ch === ',' && !q) { cols.push(cur); cur = ''; continue; }
          cur += ch;
        }
        cols.push(cur);
        const obj = {};
        headers.forEach((h, i) => obj[h] = (cols[i] || '').trim());
        return obj;
      });
    }

    function sum(rows, key) {
      return rows.reduce((a, r) => a + (parseInt(r[key] || '0', 10) || 0), 0);
    }

    async function main() {
      let meta = {};
      try { meta = await (await fetch('data/meta.json?t=' + Date.now())).json(); } catch (e) {}
      document.getElementById('updated').textContent =
        'Atualizado em: ' + (meta.updated_at || 'cache local / anterior');

      const resumo = await loadCsv('data/resumo_diario.csv');
      const coletas = await loadCsv('data/coletas.csv');

      const total = sum(resumo, 'total') || coletas.length;
      const coletada = sum(resumo, 'coletada');
      const comandada = sum(resumo, 'comandada');
      const cancelada = sum(resumo, 'cancelada');
      const cadastrada = sum(resumo, 'cadastrada');
      document.getElementById('kTotal').textContent = total;
      document.getElementById('kColetada').textContent = coletada;
      document.getElementById('kComandada').textContent = comandada;
      document.getElementById('kCancelada').textContent = cancelada;

      const labels = resumo.map(r => r.data_cadastro);
      new Chart(document.getElementById('chartResumo'), {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: 'Coletada', data: resumo.map(r => +r.coletada || 0), backgroundColor: '#22c55e' },
            { label: 'Comandada', data: resumo.map(r => +r.comandada || 0), backgroundColor: '#38bdf8' },
            { label: 'Cadastrada', data: resumo.map(r => +r.cadastrada || 0), backgroundColor: '#f59e0b' },
            { label: 'Cancelada', data: resumo.map(r => +r.cancelada || 0), backgroundColor: '#ef4444' },
          ]
        },
        options: { responsive: true, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } }
      });

      new Chart(document.getElementById('chartPizza'), {
        type: 'doughnut',
        data: {
          labels: ['Coletada', 'Comandada', 'Cadastrada', 'Cancelada'],
          datasets: [{ data: [coletada, comandada, cadastrada, cancelada],
            backgroundColor: ['#22c55e', '#38bdf8', '#f59e0b', '#ef4444'] }]
        },
        options: { plugins: { legend: { position: 'bottom' } } }
      });

      const tbody = document.getElementById('tbody');
      coletas.slice(-100).reverse().forEach(c => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${c.coleta_id || ''}</td><td>${c.situacao_atual || ''}</td>
          <td>${c.cadastrada_data || ''} ${c.cadastrada_hora || ''}</td>
          <td>${c.comandada_data || ''} ${c.comandada_hora || ''}</td>
          <td>${c.coletada_data || ''} ${c.coletada_hora || ''}</td>
          <td>${c.motorista || ''}</td><td>${c.dest_cidade || c.dest || ''}</td>`;
        tbody.appendChild(tr);
      });
    }
    main().catch(err => {
      document.getElementById('updated').textContent = 'Usando ultimo cache disponivel. ' + err;
    });
  </script>
</body>
</html>
"""


def publish_dashboard(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
    allow_push: bool = True,
) -> dict[str, Any]:
    """
    Copia CSV para dashboard/ e, se configurado, faz git commit/push.
    Em falha de push, mantém o ultimo HTML/CSV ja publicado.
    allow_push=False → só arquivos locais (modo Local / CRT).
    """
    status = on_status or _noop
    cfg = settings or load_settings()
    ensure_dashboard_files()
    paths = _copy_cache_to_dashboard()
    paths.update(_copy_contratacao_to_dashboard())
    try:
        paths.update(publish_mapa_local(on_status=status).get("paths") or {})
    except Exception as err:  # noqa: BLE001
        status(f"Mapa publish: {err}")
    result: dict[str, Any] = {"ok": True, "local": paths, "pushed": False}

    if not allow_push or not getattr(cfg, "sync_remoto", True):
        status(
            "Dashboard local atualizado"
            + (" (sync remoto OFF — sem Sheets/Pages)." if not getattr(cfg, "sync_remoto", True) else " (sem GitHub).")
        )
        result["skipped_push"] = True
        result["publish_target"] = resolve_publish_target(cfg)
        return result

    target = resolve_publish_target(cfg)
    result["publish_target"] = target
    if target == "sites":
        status(
            "Dashboard local atualizado (destino=sites — use `sync` + Google Sites; sem push)."
        )
        result["skipped_push"] = True
        return result
    if target == "local":
        status("Dashboard local atualizado (destino=local — sem push).")
        result["skipped_push"] = True
        return result

    if not github_publish_allowed(cfg):
        status("Dashboard local atualizado (publish GitHub desabilitado / destino ≠ github).")
        result["skipped_push"] = True
        return result

    if not cfg.enable_github_publish:
        status("Dashboard local atualizado (publish GitHub desabilitado).")
        result["skipped_push"] = True
        return result

    token = os.environ.get(cfg.github_token_env or "GH_TOKEN", "").strip()
    if not cfg.github_repo:
        status("Dashboard: github_repo nao configurado — so arquivo local.")
        result["skipped_push"] = True
        return result
    if not token:
        status(f"Dashboard: token {cfg.github_token_env} ausente — so arquivo local.")
        result["skipped_push"] = True
        return result

    try:
        dash = DASHBOARD_DIR
        # Se dashboard estiver dentro do repo ACE, faz commit so dos arquivos do dashboard
        repo_root = BASE_DIR
        env = os.environ.copy()
        # remote com token
        remote = f"https://x-access-token:{token}@github.com/{cfg.github_repo}.git"
        branch = cfg.github_branch or "main"

        def run(cmd: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                cmd,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

        run(["git", "add", "dashboard"])
        msg = f"chore(dashboard): atualiza dados ACE {datetime.now():%Y-%m-%d %H:%M}"
        commit = run(["git", "commit", "-m", msg])
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
            # pode nao ser repo git — tenta push de pasta isolada via gh?
            result["commit_stderr"] = commit.stderr
        # configura remote temporario se necessario
        remotes = run(["git", "remote"])
        if "origin" not in remotes.stdout:
            run(["git", "remote", "add", "origin", remote])
        else:
            run(["git", "remote", "set-url", "origin", remote])
        push = run(["git", "push", "-u", "origin", branch])
        if push.returncode == 0:
            result["pushed"] = True
            status("Dashboard publicado no GitHub.")
        else:
            result["ok"] = True  # local ok
            result["push_error"] = push.stderr
            status(f"Push GitHub falhou (mantendo pagina anterior): {push.stderr[:200]}")
        return result
    except Exception as error:  # noqa: BLE001
        result["ok"] = True
        result["error"] = str(error)
        status(f"Publish dashboard falhou (cache local ok): {error}")
        return result
