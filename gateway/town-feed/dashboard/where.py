"""Where the operator is and which events matter: pure inference (S1).

Inputs are calendar rows in the scanner's ``events`` shape (``summary``,
``start_iso``/``end_iso`` exact instants in any offset, ``all_day``,
``event_type``, ``location``, ``event_tz``, ``working_location``, …) plus the
operator profile. Nothing here reads files, the clock, or the network: ``now``
is always injected, so the plugin and the calendar scanner share one rule set.

``infer_where`` never names a city without calendar evidence for it; with no
evidence the operator is at home (the profile's ``home_city``).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

RECENT_TRAVEL_SECONDS = 36 * 60 * 60
CONNECTION_GAP_SECONDS = 12 * 60 * 60
MEANINGFUL_MIN_SECONDS = 30 * 60
MAX_EVIDENCE = 3
MAX_EVIDENCE_CHARS = 80
MAX_LABEL_CHARS = 60

# Real commitments (R3): Google's own rows plus reservations copied from mail.
COMMITMENT_EVENT_TYPES = {"default", "fromGmail"}

# IATA code -> (city, metro, IANA zone). Metro groups satellite airports with
# the city people say they live in ("Newark" lands you home in "New York").
AIRPORTS: dict[str, tuple[str, str, str]] = {
    "ATL": ("Atlanta", "Atlanta", "America/New_York"),
    "AUS": ("Austin", "Austin", "America/Chicago"),
    "BNA": ("Nashville", "Nashville", "America/Chicago"),
    "BOS": ("Boston", "Boston", "America/New_York"),
    "BUR": ("Burbank", "Los Angeles", "America/Los_Angeles"),
    "BWI": ("Baltimore", "Washington", "America/New_York"),
    "CLT": ("Charlotte", "Charlotte", "America/New_York"),
    "DAL": ("Dallas", "Dallas", "America/Chicago"),
    "DCA": ("Washington", "Washington", "America/New_York"),
    "DEN": ("Denver", "Denver", "America/Denver"),
    "DFW": ("Dallas", "Dallas", "America/Chicago"),
    "DTW": ("Detroit", "Detroit", "America/Detroit"),
    "EWR": ("Newark", "New York", "America/New_York"),
    "FLL": ("Fort Lauderdale", "Miami", "America/New_York"),
    "HNL": ("Honolulu", "Honolulu", "Pacific/Honolulu"),
    "HOU": ("Houston", "Houston", "America/Chicago"),
    "IAD": ("Washington", "Washington", "America/New_York"),
    "IAH": ("Houston", "Houston", "America/Chicago"),
    "JFK": ("New York", "New York", "America/New_York"),
    "LAS": ("Las Vegas", "Las Vegas", "America/Los_Angeles"),
    "LAX": ("Los Angeles", "Los Angeles", "America/Los_Angeles"),
    "LGA": ("New York", "New York", "America/New_York"),
    "MCO": ("Orlando", "Orlando", "America/New_York"),
    "MDW": ("Chicago", "Chicago", "America/Chicago"),
    "MIA": ("Miami", "Miami", "America/New_York"),
    "MSP": ("Minneapolis", "Minneapolis", "America/Chicago"),
    "MSY": ("New Orleans", "New Orleans", "America/Chicago"),
    "OAK": ("Oakland", "San Francisco", "America/Los_Angeles"),
    "ORD": ("Chicago", "Chicago", "America/Chicago"),
    "PDX": ("Portland", "Portland", "America/Los_Angeles"),
    "PHL": ("Philadelphia", "Philadelphia", "America/New_York"),
    "PHX": ("Phoenix", "Phoenix", "America/Phoenix"),
    "RDU": ("Raleigh", "Raleigh", "America/New_York"),
    "SAN": ("San Diego", "San Diego", "America/Los_Angeles"),
    "SEA": ("Seattle", "Seattle", "America/Los_Angeles"),
    "SFO": ("San Francisco", "San Francisco", "America/Los_Angeles"),
    "SJC": ("San Jose", "San Francisco", "America/Los_Angeles"),
    "SLC": ("Salt Lake City", "Salt Lake City", "America/Denver"),
    "TPA": ("Tampa", "Tampa", "America/New_York"),
    "YUL": ("Montreal", "Montreal", "America/Toronto"),
    "YVR": ("Vancouver", "Vancouver", "America/Vancouver"),
    "YYZ": ("Toronto", "Toronto", "America/Toronto"),
    "CUN": ("Cancun", "Cancun", "America/Cancun"),
    "MEX": ("Mexico City", "Mexico City", "America/Mexico_City"),
    "BOG": ("Bogota", "Bogota", "America/Bogota"),
    "EZE": ("Buenos Aires", "Buenos Aires", "America/Argentina/Buenos_Aires"),
    "GRU": ("Sao Paulo", "Sao Paulo", "America/Sao_Paulo"),
    "LIM": ("Lima", "Lima", "America/Lima"),
    "SCL": ("Santiago", "Santiago", "America/Santiago"),
    "AMS": ("Amsterdam", "Amsterdam", "Europe/Amsterdam"),
    "ARN": ("Stockholm", "Stockholm", "Europe/Stockholm"),
    "ATH": ("Athens", "Athens", "Europe/Athens"),
    "BCN": ("Barcelona", "Barcelona", "Europe/Madrid"),
    "BER": ("Berlin", "Berlin", "Europe/Berlin"),
    "BRU": ("Brussels", "Brussels", "Europe/Brussels"),
    "CDG": ("Paris", "Paris", "Europe/Paris"),
    "CPH": ("Copenhagen", "Copenhagen", "Europe/Copenhagen"),
    "DUB": ("Dublin", "Dublin", "Europe/Dublin"),
    "FCO": ("Rome", "Rome", "Europe/Rome"),
    "FRA": ("Frankfurt", "Frankfurt", "Europe/Berlin"),
    "GVA": ("Geneva", "Geneva", "Europe/Zurich"),
    "HEL": ("Helsinki", "Helsinki", "Europe/Helsinki"),
    "IST": ("Istanbul", "Istanbul", "Europe/Istanbul"),
    "KEF": ("Reykjavik", "Reykjavik", "Atlantic/Reykjavik"),
    "LCY": ("London", "London", "Europe/London"),
    "LGW": ("London", "London", "Europe/London"),
    "LHR": ("London", "London", "Europe/London"),
    "LIS": ("Lisbon", "Lisbon", "Europe/Lisbon"),
    "MAD": ("Madrid", "Madrid", "Europe/Madrid"),
    "MUC": ("Munich", "Munich", "Europe/Berlin"),
    "MXP": ("Milan", "Milan", "Europe/Rome"),
    "ORY": ("Paris", "Paris", "Europe/Paris"),
    "OSL": ("Oslo", "Oslo", "Europe/Oslo"),
    "PRG": ("Prague", "Prague", "Europe/Prague"),
    "STN": ("London", "London", "Europe/London"),
    "VIE": ("Vienna", "Vienna", "Europe/Vienna"),
    "WAW": ("Warsaw", "Warsaw", "Europe/Warsaw"),
    "ZRH": ("Zurich", "Zurich", "Europe/Zurich"),
    "AUH": ("Abu Dhabi", "Abu Dhabi", "Asia/Dubai"),
    "DOH": ("Doha", "Doha", "Asia/Qatar"),
    "DXB": ("Dubai", "Dubai", "Asia/Dubai"),
    "TLV": ("Tel Aviv", "Tel Aviv", "Asia/Jerusalem"),
    "BKK": ("Bangkok", "Bangkok", "Asia/Bangkok"),
    "BLR": ("Bangalore", "Bangalore", "Asia/Kolkata"),
    "BOM": ("Mumbai", "Mumbai", "Asia/Kolkata"),
    "CGK": ("Jakarta", "Jakarta", "Asia/Jakarta"),
    "DEL": ("Delhi", "Delhi", "Asia/Kolkata"),
    "DPS": ("Bali", "Bali", "Asia/Makassar"),
    "HKG": ("Hong Kong", "Hong Kong", "Asia/Hong_Kong"),
    "HND": ("Tokyo", "Tokyo", "Asia/Tokyo"),
    "ICN": ("Seoul", "Seoul", "Asia/Seoul"),
    "KIX": ("Osaka", "Osaka", "Asia/Tokyo"),
    "KUL": ("Kuala Lumpur", "Kuala Lumpur", "Asia/Kuala_Lumpur"),
    "MNL": ("Manila", "Manila", "Asia/Manila"),
    "NRT": ("Tokyo", "Tokyo", "Asia/Tokyo"),
    "PEK": ("Beijing", "Beijing", "Asia/Shanghai"),
    "PVG": ("Shanghai", "Shanghai", "Asia/Shanghai"),
    "SGN": ("Ho Chi Minh City", "Ho Chi Minh City", "Asia/Ho_Chi_Minh"),
    "SIN": ("Singapore", "Singapore", "Asia/Singapore"),
    "TPE": ("Taipei", "Taipei", "Asia/Taipei"),
    "AKL": ("Auckland", "Auckland", "Pacific/Auckland"),
    "MEL": ("Melbourne", "Melbourne", "Australia/Melbourne"),
    "SYD": ("Sydney", "Sydney", "Australia/Sydney"),
    "CAI": ("Cairo", "Cairo", "Africa/Cairo"),
    "CPT": ("Cape Town", "Cape Town", "Africa/Johannesburg"),
    "JNB": ("Johannesburg", "Johannesburg", "Africa/Johannesburg"),
    "LOS": ("Lagos", "Lagos", "Africa/Lagos"),
    "NBO": ("Nairobi", "Nairobi", "Africa/Nairobi"),
}

_CITY_ALIASES = {
    "nyc": "new york",
    "new york city": "new york",
    "manhattan": "new york",
    "brooklyn": "new york",
    "sf": "san francisco",
    "la": "los angeles",
    "dc": "washington",
    "washington dc": "washington",
    "washington d.c.": "washington",
    "são paulo": "sao paulo",
    "bengaluru": "bangalore",
    "lisboa": "lisbon",
    "roma": "rome",
    "milano": "milan",
    "münchen": "munich",
    "wien": "vienna",
    "praha": "prague",
}

# Two-letter tokens that precede numbers in ordinary titles ("PR 12",
# "CS 101", "WK 3"). A real carrier sharing one is missed, not invented.
_CODE_STOPLIST = {
    "AD", "AI", "AM", "BC", "CA", "CC", "CS", "CV", "DC", "DM", "EP", "ER", "EU", "EV",
    "FL", "GA", "GP", "HR", "ID", "IL", "IT", "LA", "LP", "MA", "MD", "ML", "MR", "NC",
    "NJ", "NO", "NY", "OK", "OR", "OS", "OT", "PA", "PE", "PG", "PM", "PO", "PR", "PS",
    "PT", "QA", "RD", "RE", "SF", "ST", "TV", "TX", "UI", "UK", "US", "UX", "VA", "VP",
    "WA", "WE", "WK",
}
_ALNUM_CARRIERS = {"B6", "F9", "G4", "U2", "W6", "6E", "3K", "5J", "D7"}
_CARRIER_NAMES = (
    "Aer Lingus", "Aeromexico", "Air Canada", "Air France", "Air New Zealand", "Alaska",
    "American", "ANA", "Avianca", "British Airways", "Cathay Pacific", "Delta", "easyJet",
    "Emirates", "Etihad", "EVA Air", "Frontier", "Hawaiian", "Iberia", "JAL", "JetBlue",
    "KLM", "Korean Air", "LATAM", "Lufthansa", "Qantas", "Qatar", "Ryanair", "Southwest",
    "Spirit", "Swiss", "TAP", "Turkish", "United", "Virgin Atlantic", "WestJet",
)

_FLIGHT_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])\s?(\d{1,4})(?![\w:])")
_CARRIER_RE = re.compile(
    r"(?<![\w])(?:(?:[A-Z][a-z]+\s+){0,2}(?:Airlines?|Airways|Air\s+Lines)|"
    + "|".join(re.escape(name) for name in _CARRIER_NAMES)
    + r")\s+(?:flight\s+)?#?(\d{1,4})(?![\w:])"
)
_IATA_ARROW_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3})\s*(?:→|->|—>|–>|⟶|✈️?)\s*([A-Z]{3})(?![A-Za-z])")
_IATA_SOFT_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3})\s*(?:-|–|—|\s+to\s+)\s*([A-Z]{3})(?![A-Za-z])")
_FLIGHT_PHRASE_RE = re.compile(
    r"\bflight\s+(?:(from)\s+(?P<from>.+?)\s+to\s+(?P<to_after_from>.+)|(?P<dir>to|from)\s+(?P<place>.+))",
    re.IGNORECASE,
)
# Detection needs the phrase to lead the title ("Flight to Lisbon", "My flight
# from Rome", "TP 202: flight to Lisbon"); "Lunch before flight to Lisbon" is
# not itself a flight.
_FLIGHT_LEAD_RE = re.compile(
    r"^\s*(?:(?:my|our|the)\s+|[A-Z0-9]{2}\s?\d{1,4}\s*[:\-–—]?\s*)?flight\s+(?:to|from)\s+\S",
    re.IGNORECASE,
)
_FLIGHT_WORD_RE = re.compile(r"\b(?:flight|flights|boarding|departs?|arrives?|layover)\b", re.IGNORECASE)
# Reminders about flights are not flights ("Book flight to Lisbon").
_FLIGHT_TODO_RE = re.compile(
    r"^\s*(?:book|buy|search|research|find|cancel|change|check|pay|plan|price|reschedule|"
    r"look|expense|review|discuss|call|email|ask|confirm|compare|pick)\b",
    re.IGNORECASE,
)
_AIRPORT_HINT_RE = re.compile(r"\b(?:airport|terminal|gate)\b", re.IGNORECASE)
_PAREN_IATA_RE = re.compile(r"\(([A-Z]{3})\)")
_TRAILING_IATA_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3})\s*$")
_PLACE_STOP_RE = re.compile(r"\s*(?:\(|\[|\s[-–—|]\s|,|\s+on\s+|\s+at\s+|\s+via\s+).*$", re.IGNORECASE)

_LODGING_RE = re.compile(
    r"\b(?:hotel|inn|resort|motel|hostel|airbnb|vrbo|lodging|marriott|hyatt|hilton|ritz|"
    r"westin|sheraton|four seasons|stay(?:ing)? at|check-?in)\b",
    re.IGNORECASE,
)
_TRIP_CITY_RE = re.compile(
    r"\b(?:trip|travel|visit|vacation|holiday|offsite|stay(?:ing)?|conference|summit)\b"
    r"[^A-Za-z]*(?:\w+\s+){0,2}?(?:to|in)\s+"
    r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2})"
)
_COUNTRY_NAMES = {
    "usa", "us", "united states", "united states of america", "uk", "united kingdom",
    "england", "france", "portugal", "spain", "italy", "germany", "japan", "singapore",
    "canada", "mexico", "netherlands", "ireland", "switzerland", "australia", "china",
    "hong kong", "korea", "south korea", "india", "brazil", "argentina", "thailand",
    "indonesia", "uae", "united arab emirates", "israel", "greece", "turkey",
}


# --- Small parsing helpers ---------------------------------------------------


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _timestamp(value: Any) -> Optional[float]:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _zone(name: Any) -> Optional[ZoneInfo]:
    text = _text(name)
    if not text:
        return None
    try:
        return ZoneInfo(text)
    except Exception:
        return None


def _span(row: dict[str, Any]) -> Optional[tuple[float, float]]:
    start = _timestamp(row.get("start_iso"))
    end = _timestamp(row.get("end_iso"))
    if start is None or end is None or end < start:
        return None
    return start, end


def _evidence(text: str) -> str:
    text = _text(text)
    return text if len(text) <= MAX_EVIDENCE_CHARS else text[: MAX_EVIDENCE_CHARS - 1].rstrip() + "…"


def _city_key(value: Any) -> str:
    key = _text(value).casefold().strip(" .")
    return _CITY_ALIASES.get(key, key)


def _cities() -> dict[str, tuple[str, str]]:
    """Known city/metro name (casefolded) -> (display city, zone)."""
    known: dict[str, tuple[str, str]] = {}
    for city, metro, zone in AIRPORTS.values():
        known.setdefault(city.casefold(), (city, zone))
        known.setdefault(metro.casefold(), (metro, zone))
    return known


_KNOWN_CITIES = _cities()
_KNOWN_CITY_RE = re.compile(
    r"(?<![A-Za-z])("
    + "|".join(re.escape(name) for name in sorted(_KNOWN_CITIES, key=len, reverse=True))
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def _endpoint_from_iata(code: str) -> dict[str, str]:
    city, metro, zone = AIRPORTS[code]
    endpoint = {"iata": code, "city": city, "timezone": zone}
    if metro != city:
        endpoint["metro"] = metro
    return endpoint


def _endpoint_from_text(text: str) -> dict[str, str]:
    place = _PLACE_STOP_RE.sub("", _text(text)).strip(" .:-")
    place = _FLIGHT_CODE_RE.sub("", place).strip(" .:-")
    if not place:
        return {}
    if place.upper() in AIRPORTS and place.isupper():
        return _endpoint_from_iata(place.upper())
    known = _KNOWN_CITIES.get(_city_key(place))
    if known:
        return {"city": known[0], "timezone": known[1]}
    if len(place) > 40 or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", place):
        return {}
    return {"city": place}


def place_matches(endpoint_or_city: Any, home_city: Any) -> bool:
    """True when a place (endpoint dict or city text) is the home city/metro."""
    home = _city_key(home_city)
    if not home:
        return False
    if isinstance(endpoint_or_city, dict):
        names = [endpoint_or_city.get("city"), endpoint_or_city.get("metro")]
    else:
        names = [endpoint_or_city]
    return any(_city_key(name) == home for name in names if _text(name))


# --- Flights -----------------------------------------------------------------


def _code_number(title: str) -> Optional[re.Match[str]]:
    for match in _FLIGHT_CODE_RE.finditer(title):
        code = match.group(1)
        if code.isalpha() and code not in _CODE_STOPLIST:
            return match
        if not code.isalpha() and code in _ALNUM_CARRIERS:
            return match
    return None


def _residue_is_route(title: str, match: re.Match[str]) -> bool:
    """Whether nothing but route words remain once the flight code is removed."""
    rest = (title[: match.start()] + " " + title[match.end():]).strip()
    for pattern in (_IATA_ARROW_RE, _IATA_SOFT_RE):
        rest = pattern.sub(" ", rest)
    rest = re.sub(r"\b(?:flight|to|from)\b.*$", " ", rest, flags=re.IGNORECASE)
    rest = re.sub(r"[()\[\]:#·•|,.\-–—→>✈️\s]+", " ", rest).strip()
    return not rest


def is_flight_title(
    title: Any,
    *,
    event_type: Any = "",
    location: Any = "",
    description: Any = "",
) -> bool:
    """Whether a calendar title (plus optional cues) describes a flight.

    Positive: "BA 117 LHR→JFK", "UA123", "Flight to Lisbon", "JFK - LIS",
    "<Carrier> 418 — LIS to BOS", Google's mail-derived flight rows. Negative:
    "Sync 22", "Q3 planning", "Book flight to Lisbon".
    """
    text = _text(title)
    if not text or _FLIGHT_TODO_RE.match(text):
        return False
    if _FLIGHT_LEAD_RE.match(text) or _IATA_ARROW_RE.search(text):
        return True
    soft = _IATA_SOFT_RE.search(text)
    if soft and (soft.group(1) in AIRPORTS or soft.group(2) in AIRPORTS):
        return True
    if _CARRIER_RE.search(text):
        return True
    cue_text = f"{_text(location)} {_text(description)}"
    has_cue = (
        _text(event_type) == "fromGmail"
        or bool(_AIRPORT_HINT_RE.search(_text(location)))
        or bool(_PAREN_IATA_RE.search(_text(location)))
        or bool(_FLIGHT_WORD_RE.search(cue_text))
    )
    match = _code_number(text)
    if match and (_residue_is_route(text, match) or has_cue):
        return True
    return bool(
        _text(event_type) == "fromGmail" and _FLIGHT_WORD_RE.search(f"{text} {_text(description)}")
    )


def flight_info(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Flight details for a calendar row, or ``None`` when it is not a flight.

    Returns ``{"code", "origin", "destination"}``; each endpoint carries
    ``iata``/``city``/``timezone`` when the title, location, or Google's
    arrival zone (``end_tz``) states them.
    """
    if not isinstance(row, dict) or row.get("all_day") is True:
        return None
    title = _text(row.get("summary"))
    location = _text(row.get("location"))
    description = _text(row.get("description_excerpt"))
    if not is_flight_title(
        title, event_type=row.get("event_type"), location=location, description=description
    ):
        return None
    origin: dict[str, str] = {}
    destination: dict[str, str] = {}
    pair = _IATA_ARROW_RE.search(title) or _IATA_SOFT_RE.search(title)
    if pair:
        first, second = pair.group(1), pair.group(2)
        origin = _endpoint_from_iata(first) if first in AIRPORTS else {"iata": first}
        destination = _endpoint_from_iata(second) if second in AIRPORTS else {"iata": second}
    phrase = _FLIGHT_PHRASE_RE.search(title)
    if phrase:
        if phrase.group("from") and phrase.group("to_after_from"):
            origin = origin or _endpoint_from_text(phrase.group("from"))
            destination = destination or _endpoint_from_text(phrase.group("to_after_from"))
        elif phrase.group("dir") and phrase.group("dir").casefold() == "to":
            destination = destination or _endpoint_from_text(phrase.group("place"))
        elif phrase.group("dir"):
            origin = origin or _endpoint_from_text(phrase.group("place"))
    if not origin and location:
        code_match = _PAREN_IATA_RE.search(location) or _TRAILING_IATA_RE.search(location)
        if code_match and code_match.group(1) in AIRPORTS:
            origin = _endpoint_from_iata(code_match.group(1))
    end_zone = _text(row.get("end_tz"))
    if end_zone and _zone(end_zone) and not destination.get("timezone"):
        if end_zone != _text(row.get("event_tz")):
            destination = dict(destination, timezone=end_zone)
    carrier = _CARRIER_RE.search(title)
    code_match = _code_number(title)
    code = ""
    if code_match:
        code = f"{code_match.group(1)} {code_match.group(2)}"
    elif carrier:
        code = _text(carrier.group(0))
    return {"code": code, "origin": origin, "destination": destination}


def _endpoint_name(endpoint: dict[str, str]) -> str:
    """How people name the place: the metro ("New York" for EWR), else city/code."""
    return endpoint.get("metro") or endpoint.get("city") or endpoint.get("iata") or ""


# --- Lodging and trips ---------------------------------------------------------


def is_lodging(row: dict[str, Any]) -> bool:
    """A hotel/rental stay: an all-day or overnight row with a lodging cue."""
    if not isinstance(row, dict) or flight_info(row) is not None:
        return False
    span = _span(row)
    if span is None:
        return False
    if row.get("lodging_hint") is True:
        return True
    text = f"{_text(row.get('summary'))} {_text(row.get('location'))}"
    if not _LODGING_RE.search(text):
        return False
    overnight = span[1] - span[0] >= 12 * 60 * 60
    return bool(row.get("all_day") is True or overnight or row.get("event_type") == "fromGmail")


def is_travel_row(row: dict[str, Any]) -> bool:
    """Rows the calendar scanner keeps in ``recent_travel`` after they end."""
    return flight_info(row) is not None or is_lodging(row)


def place_from_row(row: dict[str, Any]) -> dict[str, str]:
    """The city a stay/trip row names: known city, "trip to X", or an address."""
    summary = _text(row.get("summary"))
    location = _text(row.get("location"))
    for text in (location, summary):
        match = _KNOWN_CITY_RE.search(text)
        if match:
            city, zone = _KNOWN_CITIES[match.group(1).casefold()]
            return {"city": city, "timezone": zone}
    trip = _TRIP_CITY_RE.search(summary)
    if trip:
        endpoint = _endpoint_from_text(trip.group(1))
        if endpoint.get("city"):
            return endpoint
    segments = [segment.strip() for segment in location.split(",") if segment.strip()]
    for index, segment in enumerate(segments):
        if index == 0:
            continue
        is_state = re.fullmatch(r"[A-Z]{2}(?:\s+\d{5}(?:-\d{4})?)?", segment)
        is_country = segment.casefold().strip(".") in _COUNTRY_NAMES
        if not (is_state or is_country):
            continue
        candidate = re.sub(r"^\d[\w-]*\s+|\s+\d[\w-]*$", "", segments[index - 1]).strip()
        if index - 1 > 0 and re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,40}", candidate):
            return _endpoint_from_text(candidate) or {"city": candidate}
    return {}


# --- Meaningful events ---------------------------------------------------------


def _self_declined(row: dict[str, Any]) -> bool:
    attendees = row.get("attendees")
    return isinstance(attendees, list) and any(
        isinstance(attendee, dict)
        and attendee.get("self") is True
        and _text(attendee.get("response")) == "declined"
        for attendee in attendees
    )


def has_other_attendee(row: dict[str, Any]) -> bool:
    attendees = row.get("attendees") if isinstance(row.get("attendees"), list) else []
    entries = [attendee for attendee in attendees if isinstance(attendee, dict)]
    others = [attendee for attendee in entries if attendee.get("self") is not True]
    if others:
        return True
    try:
        count = int(row.get("attendee_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return count > len(entries)


def is_meaningful(row: dict[str, Any]) -> bool:
    """A real commitment, not a personal ping.

    Timed, a commitment type (``default``, or ``fromGmail`` reservations),
    not declined, and with another attendee, a place or call link, or at
    least 30 minutes. A 15-minute solo reminder is not meaningful.
    """
    if not isinstance(row, dict) or row.get("all_day") is True:
        return False
    if (_text(row.get("event_type")) or "default") not in COMMITMENT_EVENT_TYPES:
        return False
    if _self_declined(row):
        return False
    span = _span(row)
    if span is None:
        return False
    if has_other_attendee(row) or _text(row.get("location")) or _text(row.get("conference")):
        return True
    return span[1] - span[0] >= MEANINGFUL_MIN_SECONDS


def _identity(row: dict[str, Any], span: tuple[float, float]) -> tuple[Any, ...]:
    ical_uid = _text(row.get("ical_uid"))
    if ical_uid:
        return ("ical", ical_uid, span[0])
    return ("text", _text(row.get("summary")).casefold(), span[0], span[1])


def meaningful_counts(
    events: Iterable[dict[str, Any]], now: Any, tz: Any
) -> dict[str, int]:
    """Meaningful events still ahead (or under way) today, and all of tomorrow."""
    zone = tz if hasattr(tz, "utcoffset") else (_zone(tz) or timezone.utc)
    now_ts = _timestamp(now) or 0.0
    today = datetime.fromtimestamp(now_ts, tz=zone).date()
    counts = {"today": 0, "tomorrow": 0}
    seen: set[tuple[Any, ...]] = set()
    for row in events or ():
        if not is_meaningful(row):
            continue
        span = _span(row)
        if span is None or span[1] <= now_ts:
            continue
        identity = _identity(row, span)
        if identity in seen:
            continue
        seen.add(identity)
        delta = (datetime.fromtimestamp(span[0], tz=zone).date() - today).days
        if delta <= 0:
            counts["today"] += 1
        elif delta == 1:
            counts["tomorrow"] += 1
    return counts


def next_meaningful_event(events: Iterable[dict[str, Any]], now: Any) -> Optional[dict[str, Any]]:
    """The earliest meaningful event that has not started yet."""
    now_ts = _timestamp(now) or 0.0
    best: Optional[tuple[float, dict[str, Any]]] = None
    for row in events or ():
        if not is_meaningful(row):
            continue
        span = _span(row)
        if span is None or span[0] < now_ts:
            continue
        if best is None or span[0] < best[0]:
            best = (span[0], row)
    return best[1] if best else None


# --- Where ---------------------------------------------------------------------


def _offset_label(zone_name: str, home_zone_name: str, now_ts: float) -> str:
    """"+12h from home" style offset; empty when the clocks agree."""
    zone, home = _zone(zone_name), _zone(home_zone_name)
    if zone is None or home is None:
        return ""
    moment = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    zone_offset = moment.astimezone(zone).utcoffset()
    home_offset = moment.astimezone(home).utcoffset()
    if zone_offset is None or home_offset is None:
        return ""
    minutes = int((zone_offset - home_offset).total_seconds() // 60)
    if minutes == 0:
        return ""
    sign = "+" if minutes > 0 else "-"
    hours, remainder = divmod(abs(minutes), 60)
    return f"{sign}{hours}h{f'{remainder:02d}m' if remainder else ''} from home"


def _zone_city(zone_name: str) -> str:
    return zone_name.rsplit("/", 1)[-1].replace("_", " ") if zone_name else ""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _hours_ago(ts: float, now_ts: float) -> str:
    hours = max(0, round((now_ts - ts) / 3600))
    return "just now" if hours == 0 else f"{hours}h ago"


def _clip(text: str, limit: int = MAX_LABEL_CHARS) -> str:
    text = _text(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _result(
    status: str,
    *,
    city: str = "",
    zone: str = "",
    label: str,
    evidence: Iterable[str] = (),
    since: Optional[float] = None,
    until: Optional[float] = None,
    source: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "city": _text(city),
        "timezone": zone if _zone(zone) else "",
        "label": _clip(label),
        "evidence": [_evidence(item) for item in evidence if _text(item)][:MAX_EVIDENCE],
        "source": source,
    }
    if since is not None:
        result["since"] = _iso(since)
    if until is not None:
        result["until"] = _iso(until)
    return result


def _flight_rows(rows: Iterable[dict[str, Any]]) -> list[tuple[float, float, dict[str, Any], dict[str, Any]]]:
    flights: list[tuple[float, float, dict[str, Any], dict[str, Any]]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        info = flight_info(row)
        span = _span(row)
        if info is None or span is None:
            continue
        key = (_text(row.get("summary")).casefold(), span[0])
        if key in seen:
            continue
        seen.add(key)
        flights.append((span[0], span[1], row, info))
    flights.sort(key=lambda entry: entry[0])
    return flights


def _trip_origin(
    landed: list[tuple[float, float, dict[str, Any], dict[str, Any]]], index: int
) -> dict[str, str]:
    """Origin of the whole journey: follow connecting legs backwards."""
    start, _end, _row, info = landed[index]
    origin = info["origin"]
    for earlier in reversed(landed[:index]):
        e_start, e_end, _e_row, e_info = earlier
        if e_end <= start and start - e_end <= CONNECTION_GAP_SECONDS:
            origin = e_info["origin"] or origin
            start = e_start
    return origin


def _flight_label(info: dict[str, Any], row: dict[str, Any]) -> str:
    return info.get("code") or _clip(_text(row.get("summary")), 40)


def infer_where(
    events: Iterable[dict[str, Any]],
    recent_travel: Iterable[dict[str, Any]],
    profile: Optional[dict[str, Any]],
    now: Any,
) -> dict[str, Any]:
    """Where the operator is right now, from calendar evidence only.

    Precedence: (1) a flight today or one that landed in the last 36 h;
    (2) an active lodging or multi-day stay; (3) today's working location;
    (4) the zone of today's self-organized timed events when it differs from
    home and at least two agree. No evidence -> ``home`` in ``home_city``.
    """
    profile = profile if isinstance(profile, dict) else {}
    home_city = _text(profile.get("home_city"))
    home_zone_name = _text(profile.get("home_timezone"))
    home_zone = _zone(home_zone_name) or timezone.utc
    if not _zone(home_zone_name):
        home_zone_name = ""
    now_ts = _timestamp(now) or 0.0
    today = datetime.fromtimestamp(now_ts, tz=home_zone).date()
    event_rows = [row for row in (events or ()) if isinstance(row, dict)]
    travel_rows = [row for row in (recent_travel or ()) if isinstance(row, dict)]

    def is_home(endpoint: dict[str, str]) -> bool:
        # A named place must be the home city; without a home city (or a
        # place name) the home zone is the best evidence there is.
        if not endpoint:
            return False
        if home_city:
            if place_matches(endpoint, home_city):
                return True
            if endpoint.get("city") or endpoint.get("metro"):
                return False
        return bool(home_zone_name and endpoint.get("timezone") == home_zone_name)

    # (1) Flights.
    flights = _flight_rows(event_rows + travel_rows)
    upcoming = [
        entry
        for entry in flights
        if entry[0] <= now_ts < entry[1]
        or (entry[0] > now_ts and datetime.fromtimestamp(entry[0], tz=home_zone).date() == today)
    ]
    if upcoming:
        start, end, row, info = upcoming[0]
        destination = info["destination"]
        going_home = is_home(destination) if destination else False
        place = home_city if going_home and home_city else _endpoint_name(destination)
        in_air = start <= now_ts
        if in_air:
            label = f"In the air to {place}" if place else "In the air"
        elif going_home and place:
            label = f"Flying home to {place} today"
        else:
            label = f"Flying to {place} today" if place else "Flying today"
        # Departure time on the departure airport's clock when known.
        departure_zone = _zone(info["origin"].get("timezone")) or home_zone
        evidence = [f"{_flight_label(info, row)} {'departed' if in_air else 'departs'} "
                    f"{datetime.fromtimestamp(start, tz=departure_zone).strftime('%-I:%M %p')}"]
        if _endpoint_name(info["origin"]):
            evidence.append(f"From {_endpoint_name(info['origin'])}")
        return _result(
            "traveling_today",
            city=place if destination.get("city") or going_home else "",
            zone=destination.get("timezone", "") if destination else "",
            label=label,
            evidence=evidence,
            since=start,
            until=end,
            source="flight",
        )
    landed = [entry for entry in flights if entry[1] <= now_ts]
    recent = [entry for entry in landed if now_ts - entry[1] <= RECENT_TRAVEL_SECONDS]
    if recent:
        index = landed.index(recent[-1])
        start, end, row, info = landed[index]
        destination = info["destination"]
        origin = _trip_origin(landed, index)
        if not _endpoint_name(origin):
            stays = [
                travel for travel in travel_rows
                if is_lodging(travel) and not place_matches(place_from_row(travel), home_city)
            ]
            if stays:
                origin = place_from_row(stays[-1])
        landed_evidence = f"{_flight_label(info, row)} landed {_hours_ago(end, now_ts)}"
        if destination and is_home(destination):
            origin_name = _endpoint_name(origin)
            city = home_city or _endpoint_name(destination)
            label = f"Back in {city}" if city else "Back home"
            if origin_name and not is_home(origin):
                label += f" after {origin_name}"
            evidence = [landed_evidence]
            if origin_name:
                evidence.append(f"From {origin_name}")
            return _result(
                "just_returned",
                city=city,
                zone=home_zone_name,
                label=label,
                evidence=evidence,
                since=end,
                source="flight",
            )
        if destination.get("city") or destination.get("timezone"):
            city = destination.get("metro") or destination.get("city", "")
            zone = destination.get("timezone", "")
            offset = _offset_label(zone, home_zone_name, now_ts) if zone else ""
            base = f"In {city}" if city else f"On {_zone_city(zone)} time"
            evidence = [landed_evidence]
            if _endpoint_name(origin):
                evidence.append(f"From {_endpoint_name(origin)}")
            return _result(
                "away",
                city=city,
                zone=zone,
                label=f"{base} · {offset}" if offset else base,
                evidence=evidence,
                since=end,
                source="flight",
            )

    # (2) Active lodging or a multi-day stay somewhere named.
    for row in event_rows:
        span = _span(row)
        if span is None or not (span[0] <= now_ts < span[1]):
            continue
        lodging = is_lodging(row)
        if not lodging and not (row.get("multi_day") is True and row.get("all_day") is True):
            continue
        place = place_from_row(row)
        if place and place_matches(place, home_city):
            continue
        if not place and not lodging:
            continue
        city = place.get("city", "")
        zone = place.get("timezone", "")
        offset = _offset_label(zone, home_zone_name, now_ts) if zone else ""
        if city:
            label = f"In {city} · {offset}" if offset else f"In {city}"
        else:
            label = f"Away · {_clip(_text(row.get('summary')), 40)}"
        until_text = datetime.fromtimestamp(span[1], tz=home_zone).strftime("%a %b %-d")
        return _result(
            "away",
            city=city,
            zone=zone,
            label=label,
            evidence=[f"{_clip(_text(row.get('summary')), 50)} until {until_text}"],
            since=span[0],
            until=span[1],
            source="lodging" if lodging else "stay",
        )

    # (3) Today's working location.
    for row in event_rows:
        location = row.get("working_location")
        span = _span(row)
        if row.get("event_type") != "workingLocation" or not isinstance(location, dict) or span is None:
            continue
        covers_now = span[0] <= now_ts < span[1]
        starts_today = datetime.fromtimestamp(span[0], tz=home_zone).date() == today
        if not (covers_now or starts_today):
            continue
        kind = _text(location.get("type"))
        detail = _clip(_text(location.get("label")), 40)
        if kind == "home":
            label, evidence = "Working from home", "Calendar working location: home"
        elif kind == "office":
            label = f"At the office · {detail}" if detail else "At the office"
            evidence = f"Calendar working location: office{f' ({detail})' if detail else ''}"
        elif kind == "custom":
            place = _endpoint_from_text(detail) if detail else {}
            if place.get("timezone") and not place_matches(place, home_city):
                zone = place["timezone"]
                offset = _offset_label(zone, home_zone_name, now_ts)
                return _result(
                    "away",
                    city=place.get("city", ""),
                    zone=zone,
                    label=f"In {place['city']} · {offset}" if offset else f"In {place['city']}",
                    evidence=[f"Calendar working location: {detail}"],
                    source="working_location",
                )
            label = f"Working from {detail}" if detail else "Working away from your desk"
            evidence = f"Calendar working location: {detail or 'custom'}"
        else:
            continue
        return _result(
            "home",
            city=home_city,
            zone=home_zone_name,
            label=label,
            evidence=[evidence],
            source="working_location",
        )

    # (4) The zone today's own events were set in (organizer-set zones of
    # invitations say where the organizer is, not the operator).
    votes: dict[str, int] = {}
    home_votes = 0
    for row in event_rows:
        span = _span(row)
        zone_name = _text(row.get("event_tz"))
        if (
            span is None
            or row.get("all_day") is True
            or (_text(row.get("event_type")) or "default") not in COMMITMENT_EVENT_TYPES
            or _text(row.get("organizer"))
            or not zone_name
            or datetime.fromtimestamp(span[0], tz=home_zone).date() != today
        ):
            continue
        zone = _zone(zone_name)
        if zone is None:
            continue
        moment = datetime.fromtimestamp(span[0], tz=timezone.utc)
        if moment.astimezone(zone).utcoffset() == moment.astimezone(home_zone).utcoffset():
            home_votes += 1
            continue
        votes[zone_name] = votes.get(zone_name, 0) + 1
    if votes:
        zone_name, count = max(votes.items(), key=lambda item: item[1])
        if count >= 2 and count > home_votes:
            offset = _offset_label(zone_name, home_zone_name, now_ts)
            base = f"On {_zone_city(zone_name)} time"
            return _result(
                "away",
                zone=zone_name,
                label=f"{base} · {offset}" if offset else base,
                evidence=[f"{count} of today's events are set in {zone_name}"],
                source="event_timezones",
            )

    return _result(
        "home",
        city=home_city,
        zone=home_zone_name,
        label=f"Home in {home_city}" if home_city else "Home",
        source="default",
    )
