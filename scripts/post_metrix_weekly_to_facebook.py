"""
Skript for automatisk publisering av Facebook-innlegg om kommende runder i TFK Seriespill.

Dette skriptet:
1. Henter rundeinformasjon fra en DiscGolfMetrix-serie.
2. Finner neste runde som skal spilles (altså i morgen).
3. Henter detaljer fra runde-siden (bane, layout, beskrivelse).
4. Formaterer en informativ Facebook-post.
5. Poster meldingen til en Facebook-side ved hjelp av Graph API.

Skriptet bruker miljøvariabler `FB_PAGE_ID` og `FB_PAGE_TOKEN` for å autentisere mot Facebook.
Planlagt kjøring gjøres via GitHub Actions (se `.github/workflows/`).
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urljoin
import logging
import os

# Norsk lokalitet for korrekt datoformat (Norsk locale ikke støttet i Github Actions)
NORWEGIAN_WEEKDAYS = {
    "Monday": "mandag", "Tuesday": "tirsdag", "Wednesday": "onsdag",
    "Thursday": "torsdag", "Friday": "fredag", "Saturday": "lørdag", "Sunday": "søndag"
}

NORWEGIAN_MONTHS = {
    "January": "januar", "February": "februar", "March": "mars", "April": "april",
    "May": "mai", "June": "juni", "July": "juli", "August": "august",
    "September": "september", "October": "oktober", "November": "november", "December": "desember"
}


SERIES_URL = "https://discgolfmetrix.com/3272824&view=info"  # 2025 TFK Seriespill
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

def get_events_from_series_page(url: str):
    """
    Henter alle runder fra en Metrix-serie.

    Parameters
    ----------
    url : str
        URL til info-siden for Metrix-serien.

    Returns
    -------
    list of dict
        Liste over runder, hver med `title`, `datetime`, `url`.
    """
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    events = []
    base_url = "https://discgolfmetrix.com"

    for a in soup.select("nav.competition-selector-large ul li a[href^='/']"):
        href = a.get("href", "")
        full_url = urljoin(base_url, href)

        b_tag = a.find("b")
        if not b_tag:
            continue

        title = b_tag.text.strip()
        remaining_text = a.get_text().replace(title, "").strip()

        try:
            dt = datetime.strptime(remaining_text, "%m/%d/%y %H:%M")
        except ValueError:
            continue

        events.append({
            "title": title,
            "datetime": dt,
            "url": full_url,
        })

    return events

def find_event_for_tomorrow(events):
    """
    Finner neste runde (dvs. i morgen) fra liste over runder.

    Parameters
    ----------
    events : list of dict
        Liste med informasjon om alle runder.

    Returns
    -------
    dict or None
        Ordet runde-objekt hvis funnet, ellers None.
    """
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    for ev in events:
        if ev["datetime"].date() == tomorrow:
            return ev
    return None

def get_event_details(event):
    """
    Henter detaljert informasjon om en enkelt runde.

    Parameters
    ----------
    event : dict
        Ordet event fra `get_events_from_series_page`.

    Returns
    -------
    dict
        Detaljert info med tittel, bane, layout, beskrivelse og URL.
    """
    event_url = event["url"]
    res = requests.get(event_url)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    title_tag = soup.select_one("h1")
    title = title_tag.text.strip() if title_tag else "Ukjent tittel"

    course_a = soup.select_one("a[href^='/course/']")
    course_name = layout_name = course_full = "Ukjent bane"
    course_id = None

    if course_a:
        course_id_match = course_a.get("href", "").split("/course/")[-1]
        if course_id_match.isdigit():
            course_id = int(course_id_match)

        text = course_a.get_text(separator=" ", strip=True).replace("->", "")
        if "→" in text:
            parts = [p.strip(" -") for p in text.split("→") if p.strip()]
            if len(parts) >= 2:
                course_name = parts[0]
                layout_name = parts[1]
                course_full = f"{course_name} – {layout_name}"
            else:
                course_name = course_full = text
        else:
            course_name = course_full = text

    info_tab = soup.select_one("div.info-tab-content")
    description = info_tab.get_text("\n", strip=True) if info_tab else ""

    return {
        "title": title,
        "course_id": course_id,
        "datetime": event["datetime"],
        "course": course_name,
        "layout": layout_name,
        "course_full": course_full,
        "description": description,
        "url": event_url
    }

def format_event_post(event: dict) -> str:
    """
    Formaterer en Facebook-post for en gitt runde.

    Parameters
    ----------
    event : dict
        Ordet event med detaljert info.

    Returns
    -------
    str
        Ferdig formattert Facebook-melding.
    """
    dt = event["datetime"]
    weekday = NORWEGIAN_WEEKDAYS[dt.strftime("%A")]
    month = NORWEGIAN_MONTHS[dt.strftime("%B")]
    dt_str = f"{weekday.capitalize()} {dt.day:02d}. {month} {dt.year} kl. {dt.strftime('%H:%M')}"

    return (
        f"📣 Neste runde i TFK Seriespill nærmer seg!\n\n"
        f"🏆 {event['title']}\n"
        f"📅 {dt_str}\n"
        f"⛳ {event['course']}\n"
        f"🗺️ Layout: {event['layout']}\n\n"
        f"ℹ️ {event['description'][:200]}{'...' if len(event['description']) > 200 else ''}\n\n"
        f"🔗 Mer info og påmelding: {event['url']}"
    )

def post_to_facebook(message: str, page_id: str, access_token: str):
    """
    Poster en melding til Facebook-siden via Graph API.

    Parameters
    ----------
    message : str
        Tekstmeldingen som skal postes.
    page_id : str
        Facebook Page-ID.
    access_token : str
        Side-tilgangstoken med `pages_manage_posts`.

    Returns
    -------
    dict
        JSON-respons fra Graph API.
    """
    url = f"https://graph.facebook.com/v23.0/{page_id}/feed"
    payload = {
        "message": message,
        "access_token": access_token
    }

    response = requests.post(url, data=payload)

    if response.ok:
        logging.info("✅ Facebook-post publisert.")
        return response.json()
    else:
        logging.error("❌ Facebook-post feilet: %s", response.text)
        response.raise_for_status()


if __name__ == "__main__":
    events = get_events_from_series_page(SERIES_URL)
    event = find_event_for_tomorrow(events)

    if event:
        event_details = get_event_details(event)
        message = format_event_post(event_details)

        PAGE_ID = os.getenv("FB_PAGE_ID")
        ACCESS_TOKEN = os.getenv("FB_PAGE_TOKEN")

        post_to_facebook(message, PAGE_ID, ACCESS_TOKEN)
    else:
        print("📭 Ingen runde i morgen.")
