import os, re, smtplib, requests, feedparser
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Dict, Any, Tuple, Optional, List
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ---------------- Flags (FlagCDN for ALL; PH fallback) ----------------
def flag_url_from_code(code: Optional[str], size: int = 48) -> str:
    if not code or len(code.strip()) != 2:
        code = "ph"
    return f"https://flagcdn.com/w{size}/{code.strip().lower()}.png"

def country_flag_from_code(code: str) -> str:
    try:
        base = 127397
        return ''.join([chr(base + ord(c)) for c in (code or 'PH').upper()])
    except Exception:
        return "🇵🇭"

def country_code_from_place(place: str) -> str:
    if not place:
        return "PH"
    p = place.lower()
    mapping = {
        "philippine": "PH", "philippines": "PH", "luzon": "PH", "mindanao": "PH", "visayas": "PH",
        "japan": "JP", "okinawa": "JP", "tokyo": "JP", "hokkaido": "JP", "honshu":"JP",
        "indonesia": "ID", "sumatra": "ID", "bali": "ID", "sulawesi": "ID", "java":"ID",
        "taiwan": "TW",
        "china": "CN", "chinese": "CN",
        "usa": "US", "united states": "US", "california": "US", "alaska": "US", "hawaii": "US",
        "mexico": "MX", "mexican": "MX",
        "chile": "CL", "santiago": "CL",
        "fiji": "FJ",
        "new zealand": "NZ",
        "papua new guinea": "PG",
        "solomon": "SB",
        "vanuatu": "VU"
    }
    for k, v in mapping.items():
        if k in p:
            return v
    m = re.search(r"\b([A-Z]{2})\b", place.upper())
    if m:
        return m.group(1)
    return "PH"

# ---------------- Feeds (USGS + ORFEUS) ----------------
USGS_FEEDS = {
    "Past Hour (All)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "Past Day (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
    "Past 7 Days (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
    "Past 30 Days (M4.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson",
}
ORFEUS_QUERY = "https://api.orfeus-eu.org/fdsnws/event/1/query?format=geojson&limit=300&minmag=2.5&orderby=time"

def fetch_json(url: str) -> Dict[str, Any]:
    r = requests.get(url, timeout=12)
    r.raise_for_status()
    return r.json()

def fetch_usgs_feed(url: str) -> Dict[str, Any]:
    return fetch_json(url)

def fetch_orfeus_feed() -> Dict[str, Any]:
    return fetch_json(ORFEUS_QUERY)

# ---------------- Basic helpers ----------------
def safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def km_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    from math import radians, sin, cos, atan2, sqrt
    dlat = radians(lat2-lat1); dlon = radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def magnitude_color(m):
    try:
        m=float(m)
        if m>=7: return "#7f1d1d"
        if m>=6: return "#b91c1c"
        if m>=5: return "#ef4444"
        if m>=4: return "#f59e0b"
        if m>=3: return "#84cc16"
        return "#22c55e"
    except:
        return "#22c55e"

def moving_average(arr: List[float], window: int):
    out=[]
    for i in range(len(arr)):
        s=arr[max(0,i-window+1):i+1]
        out.append(sum(s)/len(s))
    return out

def unique_quake_id(row: Dict[str, Any]) -> str:
    key = f"{row.get('time_utc')}|{row.get('lat')}|{row.get('lon')}|{row.get('mag')}"
    return str(abs(hash(key)))

# ---------------- Geolocation (IPinfo) ----------------
def ip_geolocate() -> Tuple[Optional[float], Optional[float], str, Dict[str, Any]]:
    token = os.getenv("IPINFO_TOKEN", "").strip()
    label="IPinfo"; meta={}
    try:
        url = f"https://ipinfo.io/json?token={token}" if token else "https://ipinfo.io/json"
        r = requests.get(url, timeout=8); j = r.json()
        loc = j.get("loc","")
        lat, lon = [float(x) for x in loc.split(",")] if loc else (None, None)
        meta = {"ip": j.get("ip"), "city": j.get("city"), "region": j.get("region"),
                "country": j.get("country"), "provider": j.get("org")}
        return lat, lon, label, meta
    except Exception:
        return None, None, label, meta

# ---------------- Feed row formatter (USGS/ORFEUS) ----------------
def format_quake_row(feature: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        props = feature.get("properties", {})
        geom  = feature.get("geometry", {})
        coords = geom.get("coordinates", [])
        lon, lat, depth_km = (coords + [None, None, None])[:3]
        mag = props.get("mag")
        place = props.get("place") or props.get("flynn_region") or props.get("description") or "Unknown location"
        tms = props.get("time") or props.get("origintime")
        if isinstance(tms, (int, float)):
            time_utc = datetime.fromtimestamp(tms/1000.0, tz=timezone.utc).replace(tzinfo=None)
        else:
            try:
                time_utc = datetime.fromisoformat(re.sub("Z$", "", str(tms))).replace(tzinfo=None)
            except Exception:
                time_utc = None
        return {"lat": float(lat), "lon": float(lon), "mag": safe_float(mag), "depth_km": safe_float(depth_km),
                "place": str(place), "time_utc": time_utc}
    except Exception:
        return None

# ---------------- News (Google RSS + EMSC) ----------------
def fetch_quake_news(region: str, limit: int = 12):
    q = region.lower().replace(" ", "+")
    feeds = [
        f"https://news.google.com/rss/search?q=earthquake+{q}&hl=en-US&gl=US&ceid=US:en",
        "https://www.emsc-csem.org/service/rss/rss.php?typ=emsc&magmin=4"
    ]
    items=[]
    for url in feeds:
        try:
            d = feedparser.parse(url)
            for e in d.entries[:limit]:
                image_url = None
                if hasattr(e, "media_content") and e.media_content:
                    image_url = e.media_content[0].get("url")
                items.append({
                    "title": e.get("title", "Untitled"),
                    "link": e.get("link", ""),
                    "published": e.get("published", ""),
                    "summary": e.get("summary", ""),
                    "image": image_url
                })
        except Exception:
            continue
    return items[:limit]

# ---------------- Email alert ----------------
def send_email_alert(subject: str, body: str) -> bool:
    srv = os.getenv("SMTP_SERVER","").strip()
    port = int(os.getenv("SMTP_PORT","587"))
    user = os.getenv("SMTP_USER","").strip()
    pwd  = os.getenv("SMTP_PASS","").strip()
    tos  = os.getenv("ALERT_EMAIL_TO","").strip()
    if not (srv and port and user and pwd and tos):
        return False
    recipients = [x.strip() for x in tos.split(",") if x.strip()]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    try:
        import ssl
        ctx = ssl.create_default_context()
        with smtplib.SMTP(srv, port, timeout=15) as s:
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.sendmail(user, recipients, msg.as_string())
        return True
    except Exception:
        return False
