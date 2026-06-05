"""
Skool -> Google Calendar iCal Sync
Genera un feed .ics publico desde el calendario de Skool (IA Masters Academy)
Compatible con Google Calendar, Apple Calendar, Outlook.

Configuracion:
    SKOOL_COOKIE: variable de entorno con las cookies de sesion de Skool.
    Formato: auth_token=<jwt>; aws-waf-token=<token>; AWSALB=<valor>; AWSALBCORS=<valor>

NOTA: Si el script falla con 403 Forbidden, la SKOOL_COOKIE ha expirado.
      Renuevala en Settings > Secrets > SKOOL_COOKIE del repo.
"""

import os
import re
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

# --- Configuracion -----------------------------------------------------------
GROUP_SLUG    = "ia-masters-automations"
CALENDAR_NAME = "IA Masters Academy"
CALENDAR_DESC = "Eventos sincronizados automaticamente desde Skool"
OUTPUT_FILE   = "docs/calendar.ics"
MONTHS_AHEAD  = 6
MONTHS_BACK   = 1
SKOOL_BASE_URL = "https://www.skool.com"
REQUEST_TIMEOUT = 30

SKOOL_COOKIE = os.environ.get("SKOOL_COOKIE", "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# --- Utilidades HTTP ---------------------------------------------------------

def make_headers(extra=None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }
    if SKOOL_COOKIE:
        headers["Cookie"] = SKOOL_COOKIE
    if extra:
        headers.update(extra)
    return headers

def fetch_url(url, json_response=False, extra_headers=None):
    """Peticion HTTP. Devuelve (status_code, data)."""
    h = make_headers(extra_headers)
    if json_response:
        h["Accept"] = "application/json"
        h["x-requested-with"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
            text = raw.decode("utf-8")
            if json_response:
                return 200, json.loads(text)
            return 200, text
    except urllib.error.HTTPError as e:
        return e.code, str(e.reason)
    except Exception as e:
        return 0, str(e)

# --- Obtener eventos via API REST -------------------------------------------

def get_events_via_api(cal_date):
    url = (
        f"{SKOOL_BASE_URL}/api/calendar"
        f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    log.info(f"API REST: {url}")
    status, data = fetch_url(url, json_response=True)
    if status == 200:
        if isinstance(data, list):
            return data
        return data.get("events", [])
    log.warning(f"API REST HTTP {status} para {cal_date}: {data}")
    return None

def get_events_via_nextjs(build_id, cal_date):
    url = (
        f"{SKOOL_BASE_URL}/_next/data/{build_id}"
        f"/{GROUP_SLUG}/calendar.json"
        f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    status, data = fetch_url(url, json_response=True)
    if status == 200:
        return data.get("pageProps", {}).get("events", [])
    log.warning(f"NextJS HTTP {status} para {cal_date}: {data}")
    return []

def get_build_id():
    urls_to_try = [
        f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar",
        f"{SKOOL_BASE_URL}/{GROUP_SLUG}",
        SKOOL_BASE_URL,
    ]
    for url in urls_to_try:
        log.info(f"Buscando buildId en: {url}")
        status, html = fetch_url(url)
        if status != 200:
            log.warning(f"HTTP {status} en {url}: {html}")
            continue
        # Patron 1: buildId directo
        pat1 = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
        if pat1:
            build_id = pat1.group(1)
            log.info(f"BuildId encontrado (pat1): {build_id}")
            return build_id
        # Patron 2: __NEXT_DATA__ script
        pat2 = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'\'][^>]*>([^<]+)</script>', html)
        if pat2:
            try:
                data = json.loads(pat2.group(1))
                build_id = data.get("buildId")
                if build_id:
                    log.info(f"BuildId via __NEXT_DATA__: {build_id}")
                    return build_id
            except json.JSONDecodeError:
                pass
        # Patron 3: buildManifest
        pat3 = re.search(r'/_next/static/([^/"]+)/_buildManifest', html)
        if pat3:
            build_id = pat3.group(1)
            log.info(f"BuildId via buildManifest: {build_id}")
            return build_id
        log.warning(f"No se encontro buildId en {url}")
    return None

def get_all_events():
    now = datetime.now(timezone.utc)
    seen_ids = set()
    all_events = []
    total_months = MONTHS_BACK + MONTHS_AHEAD
    start_date = now - timedelta(days=30 * MONTHS_BACK)

    # Estrategia 1: API REST directa
    log.info("Estrategia 1: API REST directa de Skool")
    api_works = True
    forbidden_count = 0
    for month_offset in range(total_months + 1):
        target = start_date + timedelta(days=30 * month_offset)
        cal_date = target.strftime("%Y-%m-01")
        log.info(f"Obteniendo eventos para: {cal_date}")
        events = get_events_via_api(cal_date)
        if events is None:
            api_works = False
            forbidden_count += 1
            break
        for ev in events:
            ev_id = ev.get("eventId") or ev.get("id") or str(ev)
            if ev_id not in seen_ids:
                seen_ids.add(ev_id)
                all_events.append(ev)

    if api_works:
        log.info(f"API REST ok. Eventos: {len(all_events)}")
        return all_events

    # Estrategia 2: buildId de Next.js
    log.warning("API REST no disponible. Intentando con buildId...")
    all_events = []
    seen_ids = set()

    build_id = get_build_id()
    if not build_id:
        log.error("ERROR: No se pudo conectar con Skool (HTTP 403 Forbidden).")
        log.error("ACCION REQUERIDA: La SKOOL_COOKIE ha expirado.")
        log.error("  1. Inicia sesion en https://www.skool.com")
        log.error("  2. Copia las cookies de sesion desde las DevTools del navegador")
        log.error("  3. Actualiza el Secret SKOOL_COOKIE en GitHub Settings > Secrets")
        sys.exit(1)

    log.info(f"Usando buildId: {build_id}")
    for month_offset in range(total_months + 1):
        target = start_date + timedelta(days=30 * month_offset)
        cal_date = target.strftime("%Y-%m-01")
        log.info(f"Obteniendo eventos (NextJS) para: {cal_date}")
        events = get_events_via_nextjs(build_id, cal_date)
        for ev in events:
            ev_id = ev.get("eventId") or ev.get("id") or str(ev)
            if ev_id not in seen_ids:
                seen_ids.add(ev_id)
                all_events.append(ev)

    return all_events

# --- Generar ICS -------------------------------------------------------------

def escape_ics(text):
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "")
    return text

def fold_line(line):
    """RFC 5545: lineas de max 75 octetos."""
    result = []
    while len(line.encode("utf-8")) > 75:
        chunk = line[:75]
        while len(chunk.encode("utf-8")) > 75:
            chunk = chunk[:-1]
        result.append(chunk)
        line = " " + line[len(chunk):]
    result.append(line)
    return "\r\n".join(result)

def parse_skool_dt(dt_str):
    if not dt_str:
        return None
    try:
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None

def format_dt_utc(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")

def event_to_vevent(ev):
    event_id = ev.get("eventId") or ev.get("id", "unknown")
    title = ev.get("title", "Sin titulo")
    description = ev.get("description") or ev.get("body", "")
    location = ev.get("location", "")
    start_str = ev.get("startAt") or ev.get("startDate") or ev.get("start")
    end_str   = ev.get("endAt")   or ev.get("endDate")   or ev.get("end")
    start_dt = parse_skool_dt(start_str)
    end_dt   = parse_skool_dt(end_str)
    if start_dt is None:
        return None
    if end_dt is None or end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)
    uid = f"{event_id}@skool-calendar-sync"
    now_str = format_dt_utc(datetime.now(timezone.utc))
    lines = [
        "BEGIN:VEVENT",
        fold_line(f"UID:{uid}"),
        fold_line(f"DTSTAMP:{now_str}"),
        fold_line(f"DTSTART:{format_dt_utc(start_dt)}"),
        fold_line(f"DTEND:{format_dt_utc(end_dt)}"),
        fold_line(f"SUMMARY:{escape_ics(title)}"),
    ]
    if description:
        lines.append(fold_line(f"DESCRIPTION:{escape_ics(description)}"))
    if location:
        lines.append(fold_line(f"LOCATION:{escape_ics(location)}"))
    group_url = f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar"
    lines.append(fold_line(f"URL:{group_url}"))
    lines.append("END:VEVENT")
    return "\r\n".join(lines)

def generate_ics(events):
    now_str = format_dt_utc(datetime.now(timezone.utc))
    cal_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SkoolCalendarSync//EN",
        f"X-WR-CALNAME:{escape_ics(CALENDAR_NAME)}",
        f"X-WR-CALDESC:{escape_ics(CALENDAR_DESC)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:UTC",
        f"LAST-MODIFIED:{now_str}",
    ]
    added = 0
    for ev in events:
        vevent = event_to_vevent(ev)
        if vevent:
            cal_lines.append(vevent)
            added += 1
    cal_lines.append("END:VCALENDAR")
    log.info(f"Eventos incluidos en el ICS: {added}")
    return "\r\n".join(cal_lines) + "\r\n"

# --- Main --------------------------------------------------------------------

def main():
    log.info(f"=== Skool Calendar Sync - {GROUP_SLUG} ===")
    if not SKOOL_COOKIE:
        log.warning("SKOOL_COOKIE no configurada - las peticiones pueden fallar")
    else:
        log.info("Cookie de sesion: configurada")
    events = get_all_events()
    log.info(f"Total de eventos encontrados: {len(events)}")
    ics_content = generate_ics(events)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    log.info(f"Archivo ICS guardado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
