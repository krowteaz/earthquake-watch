# main.py
# Quake Watch - Real time Earthquake Monitor for Streamlit

import json
import math
import ssl
from datetime import datetime, timezone

import folium
import matplotlib.pyplot as plt
import pandas as pd
import pytz
import requests
import streamlit as st
import streamlit.components.v1 as components
from geopy.geocoders import Nominatim
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh
from timezonefinder import TimezoneFinder
import pycountry

# ------------------- Constants -------------------
USGS_FEEDS = {
    "Past Hour (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "Past Day (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "Past 7 Days (M1.0+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_week.geojson",
    "Past 7 Days (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
    "Past 7 Days (M4.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
}

GMT_REFERENCE = {
    -12: "Baker Island",
    -11: "American Samoa",
    -10: "Hawaii",
    -9: "Alaska",
    -8: "Los Angeles, Vancouver",
    -7: "Denver, Phoenix",
    -6: "Chicago, Mexico City",
    -5: "New York, Peru, Colombia",
    -4: "Santiago, Caracas",
    -3: "Buenos Aires, São Paulo",
    -2: "South Georgia",
    -1: "Azores",
    0: "London, Lisbon, Accra",
    1: "Berlin, Paris, Madrid",
    2: "Athens, Cairo, Johannesburg",
    3: "Moscow, Nairobi",
    4: "Dubai, Baku",
    5: "Pakistan, Maldives",
    6: "Bangladesh, Kazakhstan",
    7: "Thailand, Vietnam, Jakarta",
    8: "China, Singapore, Philippines",
    9: "Japan, Korea",
    10: "Sydney, Papua New Guinea",
    11: "Solomon Islands",
    12: "Fiji, New Zealand",
    13: "Samoa, Tonga",
    14: "Kiribati",
}

# ------------------- Utilities -------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def fetch_geojson(url, timeout=10):
    # Small resilient fetch with a single retry
    ctx = ssl.create_default_context()
    headers = {"User-Agent": "QuakeWatch/1.0"}
    try:
        from urllib.request import urlopen, Request
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        # Retry once via requests
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

def geo_from_ip():
    try:
        r = requests.get("https://ipinfo.io/json", timeout=5)
        data = r.json()
        if "loc" in data:
            lat, lon = map(float, data["loc"].split(","))
            city = data.get("city") or ""
            country = data.get("country") or ""
            label = ", ".join([x for x in [city, country] if x]) or "IP location"
            return lat, lon, label
    except Exception:
        pass
    return 14.5995, 120.9842, "Manila (fallback)"

def get_timezone(lat, lon):
    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lat=lat, lng=lon)
    if tz_name:
        return pytz.timezone(tz_name), tz_name
    return timezone.utc, "UTC"

def play_beep():
    # Lightweight in browser beep using Web Audio API
    components.html(
        """
        <script>
        try{
          const ctx = new (window.AudioContext || window.webkitAudioContext)();
          const o = ctx.createOscillator();
          const g = ctx.createGain();
          o.type = 'sine';
          o.frequency.value = 880;
          o.connect(g);
          g.connect(ctx.destination);
          g.gain.setValueAtTime(0.0001, ctx.currentTime);
          g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.01);
          o.start();
          g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.3);
          o.stop(ctx.currentTime + 0.35);
        }catch(e){}
        </script>
        """,
        height=0,
    )

def desktop_notify(title, body):
    components.html(
        f"""
        <script>
        try{{
          if (Notification && Notification.permission !== 'granted') {{
            Notification.requestPermission();
          }}
          if (Notification && Notification.permission === 'granted') {{
            new Notification({json.dumps(title)}, {{ body: {json.dumps(body)} }});
          }}
        }}catch(e){{}}
        </script>
        """,
        height=0,
    )

# ------------------- App -------------------
st.set_page_config(page_title="Quake Watch", layout="wide")
st.title("🌍 Quake Watch — Real time Earthquake Monitor")

# Session state
st.session_state.setdefault("seen_ids", set())
st.session_state.setdefault("page", 1)

# Sidebar — Refresh and Alerts
st.sidebar.header("⏱️ Refresh")
refresh_rate = st.sidebar.slider("Auto refresh interval (seconds)", 15, 300, 60, 15)
enable_refresh = st.sidebar.checkbox("Enable auto refresh", value=True)
if enable_refresh:
    st_autorefresh(interval=refresh_rate * 1000, key="refresh_counter")
    st.caption(f"🔄 Auto refreshing every {refresh_rate} seconds")

st.sidebar.header("🚨 Alerts")
alert_min_mag = st.sidebar.slider("Alert when magnitude ≥", 1.0, 8.0, 4.5, 0.1)
alert_sound = st.sidebar.checkbox("Play sound on alert", value=True)
alert_desktop = st.sidebar.checkbox("Desktop notification", value=False,
                                    help="Your browser will ask permission")

# Sidebar — Location
st.sidebar.header("📍 Location")
loc_mode = st.sidebar.radio("Choose location mode", ["Auto IP", "Select Country", "Manual Lat Lon"])

if loc_mode == "Auto IP":
    user_lat, user_lon, user_label = geo_from_ip()
    st.sidebar.success(f"Using location: {user_label}")
elif loc_mode == "Select Country":
    countries = sorted([c.name for c in pycountry.countries])
    country = st.sidebar.selectbox("Country", countries)
    geolocator = Nominatim(user_agent="quake_watch")
    try:
        loc = geolocator.geocode(country, timeout=10)
        if loc:
            user_lat, user_lon, user_label = loc.latitude, loc.longitude, country
        else:
            user_lat, user_lon, user_label = 14.5995, 120.9842, "Manila (fallback)"
    except Exception:
        user_lat, user_lon, user_label = 14.5995, 120.9842, "Manila (fallback)"
else:
    user_lat = st.sidebar.number_input("Latitude", value=14.5995, format="%.4f")
    user_lon = st.sidebar.number_input("Longitude", value=120.9842, format="%.4f")
    user_label = f"Custom: {user_lat:.2f}, {user_lon:.2f}"

local_tz, tz_name = get_timezone(user_lat, user_lon)

# Filters
colf1, colf2, colf3 = st.columns([2, 1, 1])
with colf1:
    feed = st.selectbox("🌐 USGS Feed", list(USGS_FEEDS.keys()))
with colf2:
    radius = st.slider("📏 Radius (km)", 50, 2000, 500, 50)
with colf3:
    min_mag = st.slider("📊 Minimum magnitude (display)", 1.0, 8.0, 3.0, 0.5)

# Time display mode
st.markdown("#### 🕒 Time Display")
time_mode = st.radio("Show times as", ["Local Time", "UTC", "Select GMT Offset"], horizontal=True)

selected_tz = None
if time_mode == "Select GMT Offset":
    options = [f"GMT{offset:+d} ({ref})" for offset, ref in GMT_REFERENCE.items()]
    default_idx = options.index("GMT+0 (London, Lisbon, Accra)")
    gmt_choice = st.selectbox("GMT offset", options, index=default_idx)
    gmt_offset = int(gmt_choice.split()[0].replace("GMT", ""))
    selected_tz = pytz.FixedOffset(gmt_offset * 60)

# Fetch and process events
raw = fetch_geojson(USGS_FEEDS[feed])
events = []
new_alerts = []

for f in raw.get("features", []):
    fid = f.get("id") or f["properties"].get("code") or f["properties"].get("ids", "")
    lon, lat = f["geometry"]["coordinates"][:2]
    mag = f["properties"]["mag"] or 0.0
    place = f["properties"]["place"] or "Unknown location"
    t_utc = datetime.utcfromtimestamp(f["properties"]["time"] / 1000).replace(tzinfo=timezone.utc)

    if time_mode == "Local Time":
        t_disp = t_utc.astimezone(local_tz)
    elif time_mode == "UTC":
        t_disp = t_utc
    else:
        t_disp = t_utc.astimezone(selected_tz or local_tz)

    dist = haversine_km(user_lat, user_lon, lat, lon)

    if mag >= min_mag and dist <= radius:
        events.append((fid, t_disp, mag, place, lat, lon, dist, t_utc))

        # New event alerts
        if fid not in st.session_state["seen_ids"] and mag >= alert_min_mag:
            new_alerts.append((fid, t_disp, mag, place, dist))

# Sort newest first
events.sort(key=lambda e: e[1], reverse=True)

# Handle alerts
if new_alerts:
    for _fid, t_disp, mag, place, dist in new_alerts[:3]:
        st.sidebar.warning(f"New quake M{mag:.1f} • {place} • {t_disp.strftime('%Y-%m-%d %H:%M:%S')} • {dist:.0f} km")
    st.toast(f"{len(new_alerts)} new earthquake(s) ≥ M{alert_min_mag:.1f} detected", icon="⚠️")
    if alert_sound:
        play_beep()
    if alert_desktop:
        first = new_alerts[0]
        desktop_notify(
            "New earthquake detected",
            f"M{first[2]:.1f} — {first[3]} — {first[1].strftime('%Y-%m-%d %H:%M:%S')}"
        )

# Update seen ids after processing to avoid double alerts on same load
for e in events:
    st.session_state["seen_ids"].add(e[0])

# Events table with pagination
st.subheader(f"📝 Earthquake events near {user_label}")

if events:
    # Pagination controls
    topcol1, topcol2, topcol3, topcol4 = st.columns([1, 1, 2, 2])
    with topcol1:
        page_size = st.selectbox("Rows", [10, 20, 50, 100], index=0)
    total_pages = max(1, math.ceil(len(events) / page_size))
    st.session_state["page"] = min(st.session_state["page"], total_pages)

    with topcol2:
        st.write(f"Page {st.session_state['page']}/{total_pages}")

    with topcol3:
        if st.button("⬅ Prev", disabled=(st.session_state["page"] <= 1)):
            st.session_state["page"] -= 1
    with topcol4:
        if st.button("Next ➡", disabled=(st.session_state["page"] >= total_pages)):
            st.session_state["page"] += 1

    start = (st.session_state["page"] - 1) * page_size
    end = start + page_size
    page_events = events[start:end]

    df = pd.DataFrame([{
        "Time": e[1].strftime("%Y-%m-%d %H:%M:%S"),
        "Magnitude": e[2],
        "Place": e[3],
        "Lat": e[4],
        "Lon": e[5],
        "Dist (km)": e[6],
    } for e in page_events])

    def mag_style(val):
        if isinstance(val, (int, float)):
            if val < 4:
                return "color: green"
            elif val < 6:
                return "color: orange"
            else:
                return "color: red; font-weight: bold"
        return ""

    styled = df.style.applymap(mag_style, subset=["Magnitude"]).format({
        "Magnitude": "{:.1f}",
        "Lat": "{:.2f}",
        "Lon": "{:.2f}",
        "Dist (km)": "{:.1f}",
    })
    st.dataframe(styled, use_container_width=True, height=400)

    # Map and Chart
    mapcol, chartcol = st.columns([2, 1])

    with mapcol:
        st.subheader("🗺️ Map")
        m = folium.Map(location=[user_lat, user_lon], zoom_start=4, tiles="CartoDB positron")
        folium.Marker(
            [user_lat, user_lon],
            tooltip=f"You: {user_label}",
            icon=folium.Icon(color="blue"),
        ).add_to(m)

        for _fid, t_disp, mag, place, lat, lon, dist, _tutc in events:
            color = "green" if mag < 4 else "orange" if mag < 6 else "red"
            folium.CircleMarker(
                [lat, lon],
                radius=4 + mag,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.7,
                tooltip=f"M{mag:.1f} {place} • {t_disp.strftime('%Y-%m-%d %H:%M:%S')}",
            ).add_to(m)
        st_folium(m, width=900, height=520)

    with chartcol:
        st.subheader("📈 Magnitude Trend (page)")
        times = [e[1] for e in page_events]
        mags = [round(e[2], 1) for e in page_events]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(times, mags, marker="o")
        ax.set_xlabel("Time")
        ax.set_ylabel("Magnitude")
        ax.grid(True, linestyle=":")
        fig.autofmt_xdate()
        st.pyplot(fig)

else:
    st.info("No earthquake events found in this range.")

st.markdown(
    "<div style='opacity:0.6'>Data © USGS Earthquake Hazards Program — feed latency can be 1 to several minutes.</div>",
    unsafe_allow_html=True,
)
