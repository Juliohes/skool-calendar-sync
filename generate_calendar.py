"""
Skool → Google Calendar iCal Sync
Genera un feed .ics público desde el calendario de Skool (IA Masters Academy)
Compatible con Google Calendar, Apple Calendar, Outlook.
"""

import re
import json
import logging
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

# ─── Configuración ────────────────────────────────────────────────────────────
GROUP_SLUG      = "ia-masters-automations"
CALENDAR_NAME   = "IA Masters Academy"
CALENDAR_DESC   = "Eventos sincronizados automáticamente desde Skool"
OUTPUT_FILE     = "docs/calendar.ics"
MONTHS_AHEAD    = 6
MONTHS_BACK     = 1
SKOOL_BASE_URL  = "https://www.skool.com"
REQUEST_TIMEOUT = 15

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SkoolCalSync/1.0)",
    "Accept": "application/json, text/html",
}

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))

def fetch_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8")

def get_build_id():
    log.info("Obteniendo buildId de Skool...")
    html = fetch_html(SKOOL_BASE_URL)
    match = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    if not match:
        raise RuntimeError("No se encontro buildId en la pagina de Skool")
    build_id = match.group(1)
    log.info(f"BuildId: {build_id}")
    return build_id

def get_events_for_timestamp(build_id, cal_date):
    url = (
        f"{SKOOL_BASE_URL}/_next/data/{build_id}"
        f"/{GROUP_SLUG}/calendar.json"
        f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    try:
        data = fetch_json(url)
        return data.get("pageProps", {}).get("events", [])
    except urllib.error.HTTPError as e:
        log.warning(f"HTTP {e.code} para calDate={cal_date}: {e.reason}")
        return []

def get_all_events(build_id):
    now = datetime.now(timezone.utc)
    seen_ids = set()
    all_events = []
    total_months = MONTHS_BACK + MONTHS_AHEAD
    start_date = now - timedelta(days=30 * MONTHS_BACK)

    for month_offset in range(total_months + 1):
        target = start_date + timedelta(days=30 * month_offset)
        cal_date = int(target.timestamp())
        log.info(f"Descargando eventos para {target.strftime('%Y-%m')}...")
        events = get_events_for_timestamp(build_id, cal_date)
        new_count = 0
        for event in events:
            key = f"{event['id']}_{event.get('occurrenceId', '')}"
            if key not in seen_ids:
                seen_ids.add(key)
                all_events.append(event)
                new_count += 1
        log.info(f"  -> {new_count} eventos nuevos (total: {len(all_events)})")

    log.info(f"Total eventos unicos obtenidos: {len(all_events)}")
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
        f"X-WR-CALDESC:{ical_escape(CALENDAR_DESC)}",
        "X-WR-TIMEZONE:Europe/Madrid",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]

    stamp = now_ical()
    group_url = f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar"

    for event in events:
        meta = event.get("metadata", {})
        title       = meta.get("title", "Sin titulo")
        description = meta.get("description", "")
        location    = parse_location(meta.get("location", ""))
        start_time  = event.get("startTime", "")
        end_time    = event.get("endTime", "")
        event_id    = event.get("id", "")
        occurrence  = event.get("occurrenceId", "")

        uid = f"{event_id}-{occurrence}@skool.{GROUP_SLUG}"
        event_url = f"{group_url}?eid={event_id}"

        full_desc = description
        if location and location.startswith("http"):
            full_desc = (
                f"{description}\nEnlace: {location}"
                if description
                else f"Enlace: {location}"
            )

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
    import os

    log.info(f"=== Skool Calendar Sync - {GROUP_SLUG} ===")

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
    log.info(f"   Primer evento: {events[0].get('startTime', '?')}")
    log.info(f"   Ultimo evento: {events[-1].get('startTime', '?')}")

if __name__ == "__main__":
    main()
