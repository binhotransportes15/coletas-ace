"""
BINHO · terminal visual (estilo fastfetch · hacker profissional).

Paleta UI: preto / branco / verde Matrix.
Arte: cubos da logo oficial convertidos em TrueColor (█▀▄).

Uso:
  python term_brand.py
  python term_brand.py demo
  python term_brand.py logo
  ace.bat brand
"""
from __future__ import annotations

import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_ROOT = Path(__file__).resolve().parent
_ASSETS = _ROOT / "assets"
_CUBES_PNG = _ASSETS / "cubes-binho.png"
_LOGO_PNG = _ASSETS / "logo-binho.png"

# ── Paleta hacker (preto / branco / verde) ──────────────────────────
G0 = (0, 255, 102)       # neon
G1 = (0, 200, 80)        # mid
G2 = (0, 140, 55)        # sombra
G3 = (20, 80, 40)        # profundo
W0 = (240, 255, 245)     # quase branco
W1 = (180, 200, 185)     # cinza-verde
DIM_RGB = (70, 90, 75)

# Cubos BINHO — cores oficiais (fallback procedural)
CUBE_A = {"top": (140, 198, 63), "lit": (0, 146, 69), "shd": (0, 100, 50)}
CUBE_B = {"top": (255, 242, 0), "lit": (251, 176, 59), "shd": (180, 120, 20)}
CUBE_C = {"top": (237, 28, 36), "lit": (193, 39, 45), "shd": (130, 25, 30)}
CUBE_D = {"top": (41, 171, 226), "lit": (45, 70, 170), "shd": (20, 25, 80)}

GREEN = CUBE_A
YELLOW = CUBE_B
RED = CUBE_C
BLUE = CUBE_D

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
HIDE = "\033[?25l"
SHOW = "\033[?25h"

_ansi_cache: dict[tuple[str, int], str] = {}


def _enable_windows_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def rgb(r: int, g: int, b: int, text: str) -> str:
    return f"\033[38;2;{r};{g};{b}m{text}{RESET}"


def g(text: str, *, bold: bool = False) -> str:
    return (BOLD if bold else "") + rgb(*G0, text) + RESET


def w(text: str, *, bold: bool = False) -> str:
    return (BOLD if bold else "") + rgb(*W0, text) + RESET


def muted(text: str) -> str:
    return rgb(*DIM_RGB, text)


def paint(face: dict[str, tuple[int, int, int]], key: str, ch: str = "█") -> str:
    r, gg, b = face[key]
    return rgb(r, gg, b, ch)


def _rgba_opaque(pixel: tuple[int, ...], *, alpha_min: int = 28, luma_min: int = 18) -> tuple[int, int, int] | None:
    if len(pixel) == 4:
        r, g, b, a = pixel
    else:
        r, g, b = pixel[:3]
        a = 255
    if a < alpha_min or (r + g + b) < luma_min:
        return None
    return (r, g, b)


def image_to_ansi(path: str | Path, *, width: int = 48) -> str:
    """
    PNG → arte TrueColor no terminal (half-block ▀ com FG+BG).
    Resolveção vertical ~2× via pares de pixels.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow necessário: pip install Pillow") from exc

    p = Path(path)
    key = (str(p.resolve()), int(width))
    cached = _ansi_cache.get(key)
    if cached is not None:
        return cached

    im = Image.open(p).convert("RGBA")
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    w0, h0 = im.size
    tw = max(8, int(width))
    th = max(2, int(round(tw * h0 / max(1, w0))))
    if th % 2:
        th += 1
    im = im.resize((tw, th), Image.Resampling.LANCZOS)
    px = im.load()

    lines: list[str] = []
    for y in range(0, th, 2):
        row: list[str] = []
        for x in range(tw):
            u = _rgba_opaque(px[x, y])
            l = _rgba_opaque(px[x, y + 1]) if y + 1 < th else None
            if u is None and l is None:
                row.append(" ")
            elif u is not None and l is not None:
                if u == l:
                    row.append(f"\033[38;2;{u[0]};{u[1]};{u[2]}m█{RESET}")
                else:
                    row.append(
                        f"\033[38;2;{u[0]};{u[1]};{u[2]}m"
                        f"\033[48;2;{l[0]};{l[1]};{l[2]}m▀{RESET}"
                    )
            elif u is not None:
                row.append(f"\033[38;2;{u[0]};{u[1]};{u[2]}m▀{RESET}")
            else:
                assert l is not None
                row.append(f"\033[38;2;{l[0]};{l[1]};{l[2]}m▄{RESET}")
        lines.append("".join(row).rstrip())

    out = "\n".join(lines)
    _ansi_cache[key] = out
    return out


def render_cubes_png(*, width: int = 48) -> str | None:
    """Cubos oficiais da logo; None se arquivo/PIL indisponível."""
    src = _CUBES_PNG if _CUBES_PNG.is_file() else _LOGO_PNG
    if not src.is_file():
        return None
    try:
        return image_to_ansi(src, width=width)
    except Exception:
        return None


def _cube_lines(pal: dict[str, tuple[int, int, int]], *, size: int = 8) -> list[str]:
    """
    Cubo isométrico denso (muitos chars pequenos: █▀▄).
    `size` = aresta em unidades (~6–10). Quanto maior, mais realista.
    """
    n = max(3, int(size))
    top, lit, shd = pal["top"], pal["lit"], pal["shd"]
    fw = n * 4 + 4
    fh = n * 6 + 4
    grid: list[list[str | None]] = [[None] * fw for _ in range(fh)]

    def plot(x: float, y: float, face: str) -> None:
        xi, yi = int(round(x)), int(round(y))
        if 0 <= yi < fh and 0 <= xi < fw:
            grid[yi][xi] = face

    # amostragem fina (mais pontos = menos jagged)
    steps = n * 5
    ox = 2 * n

    def paint_top() -> None:
        for si in range(steps + 1):
            for sj in range(steps + 1):
                i = n * si / steps
                j = n * sj / steps
                x = ox + 2 * i - 2 * j
                y = (i + j) * 2
                plot(x, y, "T")
                plot(x + 1, y, "T")

    # Faces laterais nascem na aresta frontal do topo (não do pico)
    for si in range(steps + 1):
        for sk in range(steps + 1):
            i = n * si / steps
            k = n * sk / steps
            x = 2 * i
            y = 2 * i + 2 * n + 2 * k
            plot(x, y, "L")
            plot(x + 1, y, "L")

    for sj in range(steps + 1):
        for sk in range(steps + 1):
            j = n * sj / steps
            k = n * sk / steps
            x = 4 * n - 2 * j
            y = 2 * n + 2 * j + 2 * k
            plot(x, y, "S")
            plot(x + 1, y, "S")

    paint_top()  # topo por cima nas arestas

    def col(face: str, ch: str) -> str:
        rgb_t = top if face == "T" else lit if face == "L" else shd
        return rgb(*rgb_t, ch)

    miny, maxy, minx, maxx = fh, -1, fw, -1
    for y in range(fh):
        for x in range(fw):
            if grid[y][x]:
                miny = min(miny, y)
                maxy = max(maxy, y)
                minx = min(minx, x)
                maxx = max(maxx, x)
    if maxy < 0:
        return [""]

    out: list[str] = []
    y = miny
    while y <= maxy:
        row: list[str] = []
        up = grid[y]
        lo = grid[y + 1] if y + 1 < fh else [None] * fw
        for x in range(minx, maxx + 1):
            u, l = up[x], lo[x]
            if u and l:
                row.append(col(u, "█" if u == l else "▀"))
            elif u:
                row.append(col(u, "▀"))
            elif l:
                row.append(col(l, "▄"))
            else:
                row.append(" ")
        out.append("".join(row).rstrip())
        y += 2
    return out


def _merge_h(left: list[str], right: list[str], gap: str = "  ") -> list[str]:
    h = max(len(left), len(right))
    L = left + [""] * (h - len(left))
    R = right + [""] * (h - len(right))
    return [f"{a}{gap}{b}" for a, b in zip(L, R)]


def _strip_ansi_width(s: str) -> int:
    import re

    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def logo_cubes(*, size: int = 8) -> str:
    """
    Arte esquerda do banner.
    Prefere PNG oficial (assets/cubes-binho.png); senão cubos procedurais.
    `size` controla largura (~ size*6 colunas).
    """
    width = max(28, min(72, int(size) * 6))
    png = render_cubes_png(width=width)
    if png:
        return png

    a = _cube_lines(CUBE_A, size=size)
    b = _cube_lines(CUBE_B, size=size)
    c = _cube_lines(CUBE_C, size=size)
    d = _cube_lines(CUBE_D, size=size)
    top = _merge_h(a, b, gap="  ")
    art_w = max((_strip_ansi_width(ln) for ln in a), default=8)
    shift = " " * max(2, art_w // 3)
    bot = [shift + ln for ln in _merge_h(c, d, gap="  ")]
    return "\n".join(top + bot)


def cubes_row() -> str:
    """Mini faixa dos 4 tons (status / separador)."""
    parts = []
    for pal in (CUBE_A, CUBE_B, CUBE_C, CUBE_D):
        parts.append(paint(pal, "top", "██") + paint(pal, "lit", "██") + paint(pal, "shd", "██"))
    return "  ".join(parts)


def color_swatches() -> str:
    """Faixa de blocos estilo fastfetch (tons verde/branco)."""
    tones = [G3, G2, G1, G0, W0, W1, G1, G3]
    row1 = "".join(rgb(*c, "██") for c in tones)
    row2 = "".join(rgb(*c, "██") for c in reversed(tones))
    return f"  {row1}\n  {row2}"


def rule(char: str = "─", width: int = 64) -> str:
    return muted(char * width)


def progress_bar(pct: float, *, width: int = 28, label: str = "") -> str:
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round((pct / 100.0) * width))
    chunks: list[str] = []
    for i in range(width):
        if i < filled:
            face = G0 if i < filled * 0.7 else G1
            chunks.append(rgb(*face, "█"))
        else:
            chunks.append(muted("░"))
    bar = "".join(chunks)
    tag = f" {label}" if label else ""
    return f"{bar} {g(f'{pct:5.1f}%', bold=True)}{tag}"


def status_online(text: str = "ONLINE") -> str:
    return f"{rgb(*G0, '███')} {g(text, bold=True)}"


def status_offline(text: str = "OFFLINE") -> str:
    return f"{rgb(*W1, '███')} {w(text, bold=True)}"


def status_idle(text: str = "STANDBY") -> str:
    return f"{rgb(*G2, '███')} {muted(text)}"


def status_work(text: str = "RUN") -> str:
    return f"{rgb(*G1, '███')} {g(text, bold=True)}"


def title_line(text: str) -> str:
    return f"  {g(text, bold=True)}"


def ok_line(text: str) -> str:
    return f"  {status_online('OK')}  {text}"


def err_line(text: str) -> str:
    return f"  {status_offline('ERR')}  {text}"


def info_line(text: str) -> str:
    return f"  {status_idle('·')}  {text}"


def work_line(text: str) -> str:
    return f"  {status_work('…')}  {text}"


def classify_status_msg(msg: str) -> str:
    m = (msg or "").lower()
    # "erros={}" no CICLO OK não é falha (antes caía em ERR por substring "erro")
    m_check = m.replace("erros={}", "").replace("errors={}", "")
    if any(x in m_check for x in ("falhou", " falha", "erro ", "erro:", "error", "traceback", "exception")):
        return "err"
    if "erro" in m_check and "erros" not in m_check:
        return "err"
    if any(x in m for x in ("ok", "concluido", "concluído", "atualizada", "salvo", "pronta")):
        return "ok"
    if any(x in m for x in ("abrindo", "baix", "gerando", "analis", "sync", "sheets", "login", "gravando")):
        return "work"
    return "info"


def format_status(msg: str, *, hhmmss: str = "") -> str:
    kind = classify_status_msg(msg)
    stamp = f"{muted(f'[{hhmmss}]')} " if hhmmss else ""
    body = str(msg)
    if kind == "err":
        return f"{stamp}{status_offline('ERR')}  {body}"
    if kind == "ok":
        return f"{stamp}{status_online('OK')}  {body}"
    if kind == "work":
        return f"{stamp}{status_work('…')}  {body}"
    return f"{stamp}{status_idle('·')}  {body}"


def make_on_status(prefix: str = ""):
    _enable_windows_ansi()

    def _cb(msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = format_status(msg, hhmmss=stamp)
        if prefix:
            print(f"  {muted(prefix)} {line}")
        else:
            print(f"  {line}")
        try:
            from crt_bridge import append_log, publish

            kind = classify_status_msg(msg)
            append_log(kind, msg if not prefix else f"{prefix} {msg}", source="cmd")
            publish(
                online=kind != "err",
                label="ONLINE" if kind != "err" else "ERR",
                pct=0,
                detail=str(msg)[:100],
                mode={"ok": "OK", "err": "ERR", "work": "RUN"}.get(kind, "RUN"),
            )
        except Exception:
            pass

    return _cb


def step_progress(step: int, total: int, label: str = "") -> str:
    total = max(1, int(total))
    step = max(0, min(int(step), total))
    pct = 100.0 * step / total
    return progress_bar(pct, width=24, label=label or f"{step}/{total}")


def spinner_frames() -> list[str]:
    frames = []
    for i in range(8):
        tones = [G0, G1, G2, W0]
        c = tones[i % 4]
        frames.append(rgb(*c, "███"))
    return frames


def _pad_ansi(s: str, width: int) -> str:
    cur = _strip_ansi_width(s)
    if cur >= width:
        return s
    return s + (" " * (width - cur))


def _info_kv(key: str, val: str) -> str:
    return f"{g(f'{key:<12}', bold=True)}{muted('│')} {w(val)}"


def build_info_lines(payload: dict[str, Any] | None = None) -> list[str]:
    """Painel direito estilo fastfetch (info ACE)."""
    payload = payload or {}
    now = datetime.now()
    host = platform.node() or "localhost"
    py = platform.python_version()
    sheets = "ON" if payload.get("enable_sheets") else "OFF"
    viz = "VISUAL" if not payload.get("headless", True) else "HEADLESS"
    arm = "ON" if payload.get("armazem_in_loop", True) else "OFF"
    intervalo = str(payload.get("loop_intervalo") or "5m")
    unit = str(payload.get("unit") or "—")
    user = str(payload.get("user") or "—")

    lines = [
        w(f"{user}@{host}", bold=True),
        muted("────────────────────"),
        _info_kv("OS", f"{platform.system()} {platform.release()}"),
        _info_kv("Host", host),
        _info_kv("Shell", f"python {py}"),
        _info_kv("Term", "ACE Console"),
        _info_kv("Uptime", now.strftime("%H:%M:%S")),
        _info_kv("Unit", unit),
        _info_kv("Sheets", sheets),
        _info_kv("SSW", viz),
        _info_kv("Armazém", arm),
        _info_kv("Loop", intervalo),
        muted("────────────────────"),
        f"{status_online('ACE')}  {status_online('READY') if sheets == 'ON' else status_idle('IDLE')}",
    ]
    return lines


def fetch_banner(
    payload: dict[str, Any] | None = None,
    *,
    size: int = 8,
) -> str:
    """
    Layout fastfetch: cubos BINHO (esq) + specs (dir).
    size ≈ densidade (largura da arte ≈ size*6).
    """
    art = logo_cubes(size=size).splitlines()
    info = build_info_lines(payload)
    art_w = max((_strip_ansi_width(ln) for ln in art), default=20) + 3
    h = max(len(art), len(info))
    art += [""] * (h - len(art))
    info += [""] * (h - len(info))
    rows = []
    for a, b in zip(art, info):
        rows.append(f"  {_pad_ansi(a, art_w)}  {b}")
    return "\n".join(rows)


def print_header_banner(*, subtitle: str = "OPERACIONAL · Console CMD", payload: dict[str, Any] | None = None) -> None:
    _enable_windows_ansi()
    print()
    print(fetch_banner(payload, size=8))
    print()
    print(f"  {g('BINHO', bold=True)}  {muted(subtitle)}")
    print(color_swatches())
    print(f"  {rule()}")


def loading_screen(
    title: str,
    steps: Iterable[str] | None = None,
    *,
    seconds: float = 1.6,
) -> None:
    _enable_windows_ansi()
    sys.stdout.write(HIDE)
    try:
        try:
            from crt_bridge import publish

            publish(online=True, label="BOOT", pct=0, detail=title, mode="BOOT")
        except Exception:
            pass
        print()
        print(fetch_banner(None, size=6))
        print()
        print(f"  {g(title, bold=True)}")
        print(f"  {cubes_row()}")
        print()
        frames = spinner_frames()
        step_list = list(steps or ["boot...", "link...", "sync...", "ready"])
        n = max(1, len(step_list))
        t0 = time.time()
        while True:
            elapsed = time.time() - t0
            pct = min(100.0, (elapsed / max(0.2, seconds)) * 100.0)
            idx = min(n - 1, int((pct / 100.0) * n))
            spin = frames[int(elapsed * 8) % len(frames)]
            line = f"\r  {spin}  {progress_bar(pct, width=24)}  {muted(step_list[idx])}   "
            sys.stdout.write(line)
            sys.stdout.flush()
            try:
                from crt_bridge import publish

                publish(
                    online=True,
                    label="BOOT",
                    pct=pct,
                    detail=f"{title} · {step_list[idx]}",
                    mode="BOOT",
                )
            except Exception:
                pass
            if pct >= 100.0:
                break
            time.sleep(0.05)
        print()
        print(f"  {status_online('READY')}")
        print()
        try:
            from crt_bridge import publish

            publish(online=True, label="ONLINE", pct=100, detail="ready", mode="READY")
        except Exception:
            pass
    finally:
        sys.stdout.write(SHOW)
        sys.stdout.flush()


def demo() -> None:
    _enable_windows_ansi()
    src = "PNG oficial" if _CUBES_PNG.is_file() else "procedural"
    print("\n" + muted(f"=== BINHO · fastfetch ({src}) ===") + "\n")
    print(fetch_banner({"enable_sheets": True, "headless": True, "unit": "SPO,LEO,RIS", "user": "ace"}, size=8))
    print()
    print(color_swatches())
    print()
    print("Status:", status_online(), "|", status_idle(), "|", status_offline())
    print("Progresso:")
    for p in (0, 25, 50, 75, 100):
        print(" ", progress_bar(p, label=f"job {p}%"))
    print()
    loading_screen(
        "ACE · boot sequence",
        steps=["kernel", "ssw link", "sheets bridge", "dashboard", "ready"],
        seconds=1.2,
    )


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    cmd = (args[0].lower() if args else "demo").lstrip("/")
    if cmd in {"demo", "show", "brand", "fetch", "neofetch", "fastfetch"}:
        demo()
        return 0
    if cmd in {"logo", "cubes", "png"}:
        print_header_banner()
        return 0
    if cmd in {"load", "loading"}:
        title = " ".join(args[1:]) or "boot sequence"
        loading_screen(title)
        return 0
    if cmd == "help":
        print("term_brand.py [demo|logo|loading] — BINHO PNG→ANSI (fallback procedural)")
        return 0
    demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
