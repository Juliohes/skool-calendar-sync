"""
Skool -> Google Calendar iCal Sync
Genera un feed .ics publico desde el calendario de Skool (IA Masters Academy)
Compatible con Google Calendar, Apple Calendar, Outlook.

Configuracion:
    SKOOL_COOKIE: variable de entorno con las cookies de sesion de Skool.
    Formato: auth_token=<jwt>; aws-waf-token=<token>; AWSALB=<valor>; AWSALBCORS=<valor>

NOTA: Si el script falla con 403 Forbidden, la SKOOL_COOKIE ha expirado.
      Renovar en: Settings > Secrets > SKOOL_COOKIE del repo de GitHub.
"""

import os
import re
import sys
import json
import gzip
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

def decode_response(resp):
    """Decodifica la respuesta HTTP, manejando gzip automaticamente."""
    raw = resp.read()
    encoding = resp.headers.get("Content-Encoding", "")
    if encoding == "gzip" or (raw[:2] == b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8")

def fetch_url(url, json_response=False, extra_headers=None):
    """Peticion HTTP. Devuelve (status_code, data)."""
    h = make_headers(extra_headers)
    if json_response:
        h["Accept"] = "application/json, */*"
        h["x-requested-with"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            text = decode_response(resp)
            if json_response:
                return 200, json.loads(text)
            return 200, text
    except urllib.error.HTTPError as e:
        return e.code, str(e.reason)
    except Exception as e:
        return 0, str(e)

# --- Obtener buildId de Next.js ----------------------------------------------

def get_build_id():
    """
    Obtiene el buildId buscando primero en la pagina del calendario del grupo,
    luego en la pagina del grupo, y finalmente en la home de Skool.
    """
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
            if status == 403:
                log.warning("ADVERTENCIA: Cookie expirada (403). Renueva SKOOL_COOKIE.")
                sys.exit(0)
            continue
        # Patron 1: "buildId":"xxx" en el JSON inline
        pat1 = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
        if pat1:
            build_id = pat1.group(1)
            log.info(f"BuildId encontrado en {url}: {build_id}")
            return build_id
        # Patron 2: tag <script id="__NEXT_DATA__">
        pat2 = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>', html)
        if pat2:
            try:
                data = json.loads(pat2.group(1))
                build_id = data.get("buildId")
                if build_id:
                    log.info(f"BuildId via __NEXT_DATA__ en {url}: {build_id}")
                    return build_id
            except json.JSONDecodeError:
                pass
        # Patron 3: /_next/static/BUILD_ID/_buildManifest
        pat3 = re.search(r'/_next/static/([^/"]+)/_buildManifest', html)
        if pat3:
            build_id = pat3.group(1)
            log.info(f"BuildId via buildManifest en {url}: {build_id}")
            return build_id
        log.warning(f"No se encontro buildId en {url} (html len={len(html)})")
    return None

# --- Obtener eventos ---------------------------------------------------------

def get_events_for_month(build_id, cal_date):
    """Obtiene eventos del calendario para un mes dado via Next.js data."""
    url = (
        f"{SKOOL_BASE_URL}/_next/data/{build_id}"
        f"/{GROUP_SLUG}/calendar.json"
        f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    status, data = fetch_url(url, json_response=True)
    if status == 200:
        events = data.get("pageProps", {}).get("events", [])
        log.info(f"  {cal_date}: {len(events)} eventos")
        return events
    log.warning(f"  {cal_date}: HTTP {status} - {data}")
    return []

def get_all_events(build_id):
    now = datetime.now(timezone.utc)
    seen_ids = set()
    all_events = []
    total_months = MONTHS_BACK + MONTHS_AHEAD
    start_date = now - timedelta(days=30 * MONTHS_BACK)
    for month_offset in range(total_months + 1):
        target = start_date + timedelta(days=30 * month_offset)
        cal_date = target.strftime("%Y-%m-01")
        events = get_events_for_month(build_id, cal_date)
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
    """RFC 5545: max 75 octetos por linea."""
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
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

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
    lines.append(fold_line(f"URL:{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar"))
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

    build_id = get_build_id()
    if not build_id:
        log.error("No se pudo obtener el buildId de Skool.")
        log.error("Verifica que SKOOL_COOKIE sea valida en GitHub Secrets.")
        sys.exit(1)

    log.info(f"BuildId: {build_id}")
    events = get_all_events(build_id)
    log.info(f"Total de eventos: {len(events)}")

    ics_content = generate_ics(events)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    log.info(f"ICS guardado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
