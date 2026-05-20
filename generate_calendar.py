"""
Skool -> Google Calendar iCal Sync - DEBUG VERSION
"""

import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

GROUP_SLUG      = "ia-masters-automations"
CALENDAR_NAME   = "IA Masters Academy"
CALENDAR_DESC   = "Eventos sincronizados automaticamente desde Skool"
OUTPUT_FILE     = "docs/calendar.ics"
MONTHS_AHEAD    = 6
MONTHS_BACK     = 1
SKOOL_BASE_URL  = "https://www.skool.com"
REQUEST_TIMEOUT = 20

SKOOL_COOKIE = os.environ.get("SKOOL_COOKIE", "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

def make_headers(extra=None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Referer": f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar",
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

def fetch_json_debug(url):
    req = urllib.request.Request(
        url, headers=make_headers({"Accept": "application/json"})
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            log.info(f"DEBUG raw response (first 300 chars): {raw[:300]}")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        log.error(f"HTTP {e.code}: {e.reason} | body: {body[:200]}")
        raise

def get_build_id():
    urls_to_try = [
        f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar",
        f"{SKOOL_BASE_URL}/{GROUP_SLUG}",
        SKOOL_BASE_URL,
    ]
    for url in urls_to_try:
        log.info(f"Buscando buildId en: {url}")
        try:
            html = fetch_text(url)
            match = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
            if match:
                build_id = match.group(1)
                log.info(f"BuildId encontrado: {build_id}")
                return build_id
        except Exception as e:
            log.warning(f"Fallo en {url}: {e}")
            continue
    raise RuntimeError("No se pudo obtener el buildId")

def get_events_for_timestamp(build_id, cal_date):
    url = (
        f"{SKOOL_BASE_URL}/_next/data/{build_id}"
        f"/{GROUP_SLUG}/calendar.json"
        f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    log.info(f"Fetching: {url}")
    try:
        data = fetch_json_debug(url)
        events = data.get("pageProps", {}).get("events", [])
        log.info(f"  pageProps keys: {list(data.get('pageProps', {}).keys())}")
        return events
    except Exception as e:
        log.warning(f"Error: {e}")
        return []

def get_all_events(build_id):
    now = datetime.now(timezone.utc)
    seen_ids = set()
    all_events = []
    # Solo 1 mes para debug
    cal_date = int(now.timestamp())
    log.info(f"Descargando eventos para ahora...")
    events = get_events_for_timestamp(build_id, cal_date)
    log.info(f"  -> {len(events)} eventos")
    all_events.extend(events)
    log.info(f"Total: {len(all_events)}")
    return all_events

def parse_location(location_str):
    if not location_str:
        return ""
    try:
        loc = json.loads(location_str)
        info = loc.get("location_info", "")
        return info if isinstance(info, str) else str(info)
    except (json.JSONDecodeError, TypeError):
        return str(location_str)

def to_ical_dt(iso_str):
    dt = datetime.fromisoformat(iso_str)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")

def now_ical():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def ical_escape(text):
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return text

def ical_fold(line):
    result = []
    encoded = line.encode("utf-8")
    while len(encoded) > 75:
        cut = 75
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        result.append(encoded[:cut].decode("utf-8"))
        encoded = b" " + encoded[cut:]
    result.append(encoded.decode("utf-8"))
    return "\r\n".join(result)

def generate_ical(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//SkoolCalSync//{GROUP_SLUG}//EN",
        f"X-WR-CALNAME:{ical_escape(CALENDAR_NAME)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    stamp = now_ical()
    group_url = f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar"
    for event in events:
        meta = event.get("metadata", {})
        title = meta.get("title", "Sin titulo")
        description = meta.get("description", "")
        location = parse_location(meta.get("location", ""))
        start_time = event.get("startTime", "")
        end_time = event.get("endTime", "")
        event_id = event.get("id", "")
        occurrence = event.get("occurrenceId", "")
        uid = f"{event_id}-{occurrence}@skool.{GROUP_SLUG}"
        event_url = f"{group_url}?eid={event_id}"
        full_desc = description
        if location and location.startswith("http"):
            full_desc = f"{description}\nEnlace: {location}" if description else f"Enlace: {location}"
        vevent_lines = [
            "BEGIN:VEVENT",
            ical_fold(f"UID:{uid}"),
            ical_fold(f"DTSTAMP:{stamp}"),
            ical_fold(f"DTSTART:{to_ical_dt(start_time)}"),
            ical_fold(f"DTEND:{to_ical_dt(end_time)}"),
            ical_fold(f"SUMMARY:{ical_escape(title)}"),
            ical_fold(f"DESCRIPTION:{ical_escape(full_desc)}"),
            ical_fold(f"URL:{event_url}"),
        ]
        if location:
            vevent_lines.append(ical_fold(f"LOCATION:{ical_escape(location)}"))
        vevent_lines.append("END:VEVENT")
        lines.extend(vevent_lines)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

def main():
    log.info(f"=== Skool Calendar Sync DEBUG - {GROUP_SLUG} ===")
    if SKOOL_COOKIE:
        log.info(f"Cookie configurada (longitud: {len(SKOOL_COOKIE)} chars)")
        log.info(f"Cookie empieza con: {SKOOL_COOKIE[:30]}...")
    else:
        log.warning("Cookie NO configurada")

    build_id = get_build_id()
    events = get_all_events(build_id)

    if not events:
        log.error("No se obtuvieron eventos. Abortando.")
        raise SystemExit(1)

    events.sort(key=lambda e: e.get("startTime", ""))
    ical_content = generate_ical(events)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(ical_content)

    log.info(f"Archivo generado: {OUTPUT_FILE} ({len(events)} eventos)")

if __name__ == "__main__":
    main()
