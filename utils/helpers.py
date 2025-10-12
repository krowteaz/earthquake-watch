
import requests, re
from datetime import datetime, timezone
import hashlib
import time
from urllib.parse import quote_plus, urlsplit, urlunsplit
import feedparser

USGS_FEEDS = {
    "Past Hour (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "Past Day (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "Past 7 Days (M1.0+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_week.geojson",
    "Past 7 Days (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
    "Past 7 Days (M4.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
    "Past 30 Days (M4.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson"
}

def fetch_usgs_feed(url: str) -> dict:
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None

def km_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    from math import radians, sin, cos, sqrt, atan2
    dlat = radians(lat2-lat1)
    dlon = radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    c = 2*atan2(sqrt(a), sqrt(1-a))
    return R*c

def format_quake_row(feature: dict, local_tz) -> dict | None:
    try:
        props = feature["properties"]
        geom = feature["geometry"]
        coords = geom["coordinates"]
        lon, lat, depth_km = coords[0], coords[1], coords[2] if len(coords) > 2 else None
        mag = props.get("mag")
        place = props.get("place", "Unknown location")
        ts_ms = props.get("time")
        if ts_ms is None:
            return None

        dt_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return {
            "id": props.get("ids") or props.get("code") or "",
            "mag": mag,
            "place": place,
            "time_utc": dt_utc.replace(tzinfo=None),
            "time_local": None,
            "time_local_str": "",
            "depth_km": depth_km,
            "lat": lat,
            "lon": lon,
            "distance_km": None
        }
    except Exception:
        return None

def magnitude_color(m):
    try:
        m = float(m)
    except Exception:
        return "gray"
    if m < 2.5:   return "#4CAF50"
    if m < 3.5:   return "#8BC34A"
    if m < 4.5:   return "#FFC107"
    if m < 5.5:   return "#FF9800"
    if m < 6.5:   return "#F44336"
    if m < 7.5:   return "#9C27B0"
    return "#B71C1C"

def distance_bucket(dist_km):
    if dist_km is None: return ("Unknown", "#9E9E9E", "⚪")
    try: d = float(dist_km)
    except Exception: return ("Unknown", "#9E9E9E", "⚪")
    if d <= 300: return ("Local Critical", "#E53935", "🔴")
    if d <= 600: return ("Regional Watch", "#FFEB3B", "🟡")
    if d <= 1000: return ("Extended Zone", "#2196F3", "🔵")
    return ("Distant Info", "#4CAF50", "🟢")

def country_code_from_place(place: str) -> str | None:
    if not place: return None
    p = place.lower()
    mapping = {
        "philippines": "PH", "luzon": "PH", "mindoro": "PH", "mindanao": "PH", "visayas": "PH",
        "japan": "JP", "honshu": "JP", "hokkaido": "JP", "kyushu": "JP",
        "indonesia": "ID", "sumatra": "ID", "sulawesi": "ID", "java": "ID", "papua": "ID",
        "taiwan": "TW", "china": "CN", "mexico": "MX", "chile": "CL", "fiji": "FJ",
        "new zealand": "NZ", "solomon islands": "SB", "vanuatu": "VU",
        "alaska": "US", "california": "US", "nevada": "US", "oregon": "US", "washington": "US", "hawaii": "US", "usa": "US", "u.s.": "US",
        "russia": "RU", "kuril": "RU", "turkey": "TR", "greece": "GR", "italy": "IT",
        "papua new guinea": "PG", "pakistan": "PK", "afghanistan": "AF", "iran": "IR",
        "india": "IN", "peru": "PE", "argentina": "AR"
    }
    for key, val in mapping.items():
        if key in p: return val
    parts = [x.strip() for x in place.split(",")]
    if len(parts) >= 2:
        last = parts[-1].lower()
        for key, val in mapping.items():
            if key == last: return val
    return None

def country_flag_from_place(place: str) -> str:
    code = country_code_from_place(place)
    if not code: return "🏳️"
    return chr(0x1F1E6 + ord(code[0]) - ord('A')) + chr(0x1F1E6 + ord(code[1]) - ord('A'))

def flag_url_from_code(code: str | None, size: int = 24, provider: str = "flagpedia") -> str | None:
    if not code: return None
    code = code.lower()
    if provider == "flagcdn":
        return f"https://flagcdn.com/24x18/{code}.png"
    return f"https://flagpedia.net/data/flags/24x18/{code}.png"

def moving_average(values, window=5):
    if window <= 1: return values
    out = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        subset = values[start:i+1]
        out.append(sum(subset) / len(subset))
    return out

def unique_quake_id(row) -> str:
    s = f"{row.get('time_utc')}-{row.get('lat')}-{row.get('lon')}-{row.get('mag')}"
    return hashlib.sha256(s.encode()).hexdigest()[:16]

# ---- IP Geolocation ----
def ip_geolocate():
    services = [
        ("ipapi", "https://ipapi.co/json/"),
        ("ipinfo", "https://ipinfo.io/json"),
        ("ipwhois", "https://ipwho.is/"),
    ]
    for name, url in services:
        try:
            r = requests.get(url, timeout=6)
            j = r.json()
            lat = lon = None
            meta = {"provider": name}
            if name == "ipapi":
                lat, lon = j.get("latitude"), j.get("longitude")
                meta.update({"ip": j.get("ip"), "city": j.get("city"), "region": j.get("region"), "country": j.get("country_name")})
            elif name == "ipinfo":
                loc = j.get("loc")
                if loc and isinstance(loc, str) and "," in loc:
                    parts = loc.split(",")
                    lat, lon = float(parts[0]), float(parts[1])
                meta.update({"ip": j.get("ip"), "city": j.get("city"), "region": j.get("region"), "country": j.get("country")})
            elif name == "ipwhois":
                lat, lon = j.get("latitude"), j.get("longitude")
                meta.update({"ip": j.get("ip"), "city": j.get("city"), "region": j.get("region"), "country": j.get("country")})
            if lat is not None and lon is not None:
                return float(lat), float(lon), name, meta
        except Exception:
            continue
    return None, None, None, None

# ---- News Feed ----
def region_to_query(region: str) -> str:
    base = "earthquake OR seismic"
    extras = {
        "Worldwide": "",
        "Philippines": " (Philippines OR PH OR Luzon OR Visayas OR Mindanao OR lindol)",
        "Japan": " (Japan OR Honshu OR Hokkaido OR Kyushu)",
        "Indonesia": " (Indonesia OR Sumatra OR Java OR Sulawesi OR Papua)",
        "Taiwan": " (Taiwan)",
        "USA West": " (California OR Alaska OR Oregon OR Washington)",
        "Mexico": " (Mexico)",
        "Chile": " (Chile)",
        "New Zealand": " (\"New Zealand\" OR NZ)",
        "Fiji": " (Fiji)",
    }
    return base + extras.get(region, "")

_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
_META_OG_IMG_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_META_TW_IMG_RE = re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

def _normalize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        scheme = parts.scheme or "https"
        return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return url

def _extract_image_from_entry(e):
    try:
        mc = getattr(e, "media_content", None)
        if mc and isinstance(mc, list):
            for item in mc:
                url = item.get("url")
                if url: return _normalize_url(url)
    except Exception:
        pass
    try:
        mt = getattr(e, "media_thumbnail", None)
        if mt and isinstance(mt, list):
            for item in mt:
                url = item.get("url")
                if url: return _normalize_url(url)
    except Exception:
        pass
    try:
        for enc in getattr(e, "enclosures", []):
            if isinstance(enc, dict):
                href = enc.get("href")
                typ = enc.get("type", "")
                if href and (typ.startswith("image/") or href.lower().endswith((".jpg",".jpeg",".png",".webp",".gif"))):
                    return _normalize_url(href)
    except Exception:
        pass
    try:
        s = getattr(e, "summary", None)
        if s:
            m = _IMG_RE.search(s)
            if m: return _normalize_url(m.group(1))
    except Exception:
        pass
    return None

def _fetch_open_graph_image(url: str) -> str | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=6, allow_redirects=True)
        html = r.text[:200000]
        m = _META_OG_IMG_RE.search(html) or _META_TW_IMG_RE.search(html)
        if m:
            img = m.group(1).strip()
            if img.startswith("//"):
                img = "https:" + img
            return _normalize_url(img)
    except Exception:
        return None
    return None

def fetch_quake_news(region: str, limit: int = 12):
    q = region_to_query(region)
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
    try:
        feed = feedparser.parse(url)
        items = []
        for e in feed.entries[:limit]:
            title = e.title if hasattr(e, "title") else "Untitled"
            link = e.link if hasattr(e, "link") else None
            source = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None
            if not source and hasattr(e, "source"):
                try:
                    source = e.source.title
                except Exception:
                    source = None
            published = None
            if hasattr(e, "published_parsed") and e.published_parsed:
                published = time.strftime("%Y-%m-%d %H:%M", e.published_parsed)
            summary = None
            if hasattr(e, "summary"):
                summary = e.summary
                if len(summary) > 320:
                    summary = summary[:317] + "…"
            image = _extract_image_from_entry(e)
            if not image and link:
                image = _fetch_open_graph_image(link)
            items.append({"title": title, "link": link, "source": source, "published": published, "summary": summary, "image": image})
        return items
    except Exception:
        return []
