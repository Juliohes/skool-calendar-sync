"""
Skool -> Google Calendar iCal Sync
Genera un feed .ics publico desde el calendario de Skool (IA Masters Academy)
Compatible con Google Calendar, Apple Calendar, Outlook.

Configuracion:
    SKOOL_COOKIE: variable de entorno con las cookies de sesion de Skool.
        Formato: auth_token=<jwt>; aws-waf-token=<token>; AWSALB=<valor>; AWSALBCORS=<valor>
        """

import os
import re
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
                "Accept": "application/json, text/html, */*",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                "Referer": f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar",
                "Origin": SKOOL_BASE_URL,
      }
      if SKOOL_COOKIE:
                headers["Cookie"] = SKOOL_COOKIE
            if extra:
                      headers.update(extra)
                  return headers

def fetch_text(url):
      req = urllib.request.Request(url, headers=make_headers())
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
              return resp.read().decode("utf-8")

def fetch_json(url, extra_headers=None):
      req = urllib.request.Request(
          url, headers=make_headers(extra_headers or {"Accept": "application/json"})
)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
              return json.loads(resp.read().decode("utf-8"))

# --- Obtener eventos via API REST de Skool -----------------------------------

def get_events_via_api(cal_date):
      """
          Usa la API REST interna de Skool para obtener eventos del calendario.
              No requiere buildId de Next.js.
                  Endpoint: /api/calendar?calDate=YYYY-MM-01&group=GROUP_SLUG
                      """
    url = (
              f"{SKOOL_BASE_URL}/api/calendar"
              f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    log.info(f"Consultando API REST: {url}")
    try:
              data = fetch_json(url, {"Accept": "application/json", "x-requested-with": "XMLHttpRequest"})
              events = data.get("events", [])
              if not events and isinstance(data, list):
                            events = data
                        return events
except urllib.error.HTTPError as e:
        log.warning(f"API REST HTTP {e.code} para calDate={cal_date}: {e.reason}")
        return None
except Exception as e:
        log.warning(f"Error en API REST para calDate={cal_date}: {e}")
        return None

def get_events_via_nextjs(build_id, cal_date):
      """
          Fallback: obtiene eventos via Next.js data endpoint.
              Requiere un buildId valido.
                  """
    url = (
              f"{SKOOL_BASE_URL}/_next/data/{build_id}"
              f"/{GROUP_SLUG}/calendar.json"
              f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    try:
              data = fetch_json(url)
        events = data.get("pageProps", {}).get("events", [])
        return events
except urllib.error.HTTPError as e:
        log.warning(f"NextJS HTTP {e.code} para calDate={cal_date}: {e.reason}")
        return []
except Exception as e:
        log.warning(f"Error NextJS para calDate={cal_date}: {e}")
        return []

def get_build_id():
      """
          Obtiene el buildId de Next.js desde el HTML de Skool.
      Se usa solo como fallback si la API REST no funciona.
          """
    urls_to_try = [
              f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar",
              f"{SKOOL_BASE_URL}/{GROUP_SLUG}",
              SKOOL_BASE_URL,
    ]
    for url in urls_to_try:
              log.info(f"Buscando buildId en: {url}")
              try:
                            html = fetch_text(url)
                            # Patron 1: "buildId":"xxx"
                            match = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
            if match:
                              build_id = match.group(1)
                              log.info(f"BuildId encontrado (patron 1): {build_id}")
                              return build_id
                          # Patron 2: __NEXT_DATA__ script tag completo
                          match2 = re.search(
                                            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>',
                                            html
                          )
            if match2:
                              try:
                                                    data = json.loads(match2.group(1))
                                                    build_id = data.get("buildId")
                                                    if build_id:
                                                                              log.info(f"BuildId encontrado via __NEXT_DATA__: {build_id}")
                                                                              return build_id
                              except json.JSONDecodeError:
                                                    pass
                                            # Patron 3: /_next/static/BUILD_ID/_buildManifest
                                            match3 = re.search(r'/_next/static/([^/"]+)/_buildManifest', html)
            if match3:
                              build_id = match3.group(1)
                log.info(f"BuildId encontrado via buildManifest: {build_id}")
                return build_id
            log.warning(f"No se encontro buildId en {url}")
except Exception as e:
            log.warning(f"Fallo al acceder a {url}: {e}")
            continue
    return None

def get_all_events():
      now = datetime.now(timezone.utc)
    seen_ids = set()
    all_events = []
    total_months = MONTHS_BACK + MONTHS_AHEAD
    start_date = now - timedelta(days=30 * MONTHS_BACK)

    # Intentar primero con la API REST directa
    log.info("Estrategia 1: API REST directa de Skool")
    api_works = False
    for month_offset in range(total_months + 1):
              target = start_date + timedelta(days=30 * month_offset)
        cal_date = target.strftime("%Y-%m-01")
        log.info(f"Obteniendo eventos para: {cal_date}")
        events = get_events_via_api(cal_date)
        if events is not None:
                      api_works = True
            for ev in events:
                              ev_id = ev.get("eventId") or ev.get("id") or str(ev)
                if ev_id not in seen_ids:
                                      seen_ids.add(ev_id)
                                      all_events.append(ev)
else:
            api_works = False
              break

    if api_works:
              log.info(f"API REST funciono correctamente. Eventos: {len(all_events)}")
        return all_events

    # Fallback: intentar con buildId de Next.js
    log.warning("API REST no disponible. Intentando con buildId de Next.js...")
    all_events = []
    seen_ids = set()

    build_id = get_build_id()
    if not build_id:
              raise RuntimeError(
                            "No se pudo obtener eventos: "
                            "ni la API REST de Skool ni el buildId de Next.js funcionaron. "
                            "Verifica que SKOOL_COOKIE sea valida y no haya expirado."
              )

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
      """RFC 5545: lineas de max 75 octetos, continuas con CRLF + espacio."""
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
