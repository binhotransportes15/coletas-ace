"""Cliente HTTP para Google Apps Script Web App (POST + redirect echo)."""
from __future__ import annotations

import http.cookiejar
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Nao seguir 302 automaticamente (urllib manteria POST e quebraria o echo)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, N802
        return None


def _parse_apps_script_response(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"ok": True}
    # Echo as vezes manda BOM / prefixo
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except json.JSONDecodeError:
        low = text.lower()
        if "<html" in low or "<!doctype" in low:
            return {
                "ok": False,
                "error": (
                    "resposta HTML do Google no echo (cookies/redirect). "
                    "Confirme apps_script_url terminando em /exec, acesso "
                    "'Qualquer pessoa', e publique Nova versao do Code.gs."
                ),
            }
        return {"ok": False, "error": f"resposta nao-JSON: {text[:200]}"}


def _build_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        _NoRedirect,
        urllib.request.HTTPSHandler(context=ctx),
    )


def _extract_location(headers: Any) -> str:
    if headers is None:
        return ""
    loc = headers.get("Location") or headers.get("location") or ""
    if loc:
        return str(loc).strip()
    # http.client.HTTPMessage: get_all
    try:
        vals = headers.get_all("Location") or headers.get_all("location") or []
        if vals:
            return str(vals[0]).strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _get_echo(opener: urllib.request.OpenerDirector, echo_url: str, *, timeout: int) -> dict[str, Any]:
    """GET no Location do 302, seguindo redirects secundarios (302 em cadeia)."""
    current = echo_url
    for _ in range(6):
        req = urllib.request.Request(
            current,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "ACE-SheetsSync/1.0",
            },
            method="GET",
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return _parse_apps_script_response(raw)
        except urllib.error.HTTPError as err:
            code = int(err.code)
            loc = _extract_location(err.headers)
            body = ""
            try:
                body = err.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            try:
                err.close()
            except Exception:  # noqa: BLE001
                pass
            if loc and code in {301, 302, 303, 307, 308}:
                current = urllib.parse.urljoin(current, loc)
                continue
            if body.strip().startswith("{") or body.strip().startswith("["):
                return _parse_apps_script_response(body)
            if "<html" in body.lower() or "<!doctype" in body.lower():
                return {
                    "ok": False,
                    "error": (
                        f"HTTP {code}: resposta HTML do Google no echo. "
                        "Confira apps_script_url (/exec) e publique Nova versao "
                        "(acesso: Qualquer pessoa)."
                    ),
                }
            return {"ok": False, "error": f"HTTP {code}: {(body or '')[:300]}"}
    return {"ok": False, "error": "muitos redirects no echo do Apps Script"}


def post_apps_script(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int = 180,
    retries: int = 3,
) -> dict[str, Any]:
    """
    POST para Web App do Apps Script.

    Google sempre responde 302 para script.googleusercontent.com/macros/echo.
    O POST ja executou o doPost; o GET no Location devolve o JSON — e precisa
    dos cookies da resposta 302 (CookieJar).
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "apps_script_url vazio"}
    # Evita /dev (so dono) e paths estranhos
    if "/macros/s/" not in url or not url.rstrip("/").endswith("/exec"):
        if "/exec" not in url:
            return {
                "ok": False,
                "error": "apps_script_url deve terminar em /exec (App da Web publicada).",
            }

    last: dict[str, Any] = {"ok": False, "error": "falha desconhecida"}
    for attempt in range(1, max(1, retries) + 1):
        opener = _build_opener()
        # text/plain evita preflight; Code.gs le e.postData.contents como JSON
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "text/plain;charset=utf-8",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "ACE-SheetsSync/1.0",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with opener.open(req, timeout=timeout) as resp:
                # Raro: algumas implantações respondem 200 direto
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = _parse_apps_script_response(raw)
                if parsed.get("ok"):
                    return parsed
                last = parsed
        except urllib.error.HTTPError as error:
            code = int(error.code)
            loc = _extract_location(error.headers)
            # Consome corpo (liberta conexão) mas cookies ja estao no jar
            try:
                error.read()
            except Exception:  # noqa: BLE001
                pass
            try:
                error.close()
            except Exception:  # noqa: BLE001
                pass

            if loc and code in {301, 302, 303, 307, 308}:
                echo = urllib.parse.urljoin(url, loc)
                # Pequena pausa: echo as vezes ainda nao esta pronto sob carga
                if attempt > 1:
                    time.sleep(0.4 * attempt)
                parsed = _get_echo(opener, echo, timeout=timeout)
                if parsed.get("ok"):
                    return parsed
                last = parsed
            else:
                last = {
                    "ok": False,
                    "error": (
                        f"HTTP {code}: falha no POST Apps Script. "
                        "Confira apps_script_url (/exec) e publique Nova versao "
                        "(acesso: Qualquer pessoa)."
                    ),
                }
        except Exception as err:  # noqa: BLE001
            last = {"ok": False, "error": str(err)}

        if attempt < retries:
            time.sleep(1.2 * attempt)
            # Fallback: form-urlencoded com payload= (extrairDados_ ja suporta)
            if attempt == 2:
                try:
                    opener = _build_opener()
                    form = urllib.parse.urlencode(
                        {"payload": json.dumps(payload, ensure_ascii=False)}
                    ).encode("utf-8")
                    req2 = urllib.request.Request(
                        url,
                        data=form,
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                            "Accept": "application/json, text/plain, */*",
                            "User-Agent": "ACE-SheetsSync/1.0",
                        },
                        method="POST",
                    )
                    try:
                        with opener.open(req2, timeout=timeout) as resp:
                            parsed = _parse_apps_script_response(
                                resp.read().decode("utf-8", errors="replace")
                            )
                            if parsed.get("ok"):
                                return parsed
                            last = parsed
                    except urllib.error.HTTPError as error:
                        loc = _extract_location(error.headers)
                        try:
                            error.read()
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            error.close()
                        except Exception:  # noqa: BLE001
                            pass
                        if loc:
                            parsed = _get_echo(
                                opener, urllib.parse.urljoin(url, loc), timeout=timeout
                            )
                            if parsed.get("ok"):
                                return parsed
                            last = parsed
                except Exception as err:  # noqa: BLE001
                    last = {"ok": False, "error": str(err)}

    return last
