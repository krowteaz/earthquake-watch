import streamlit as st
import requests, json, math, ssl, time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
import pycountry
import folium
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
import pandas as pd
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import pytz
import streamlit.components.v1 as components

# ------------------- Constants -------------------
USGS_FEEDS = {
    "Past Hour (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "Past Day (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "Past 7 Days (M1.0+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_week.geojson",
    "Past 7 Days (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
    "Past 7 Days (M4.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
}

# ------------------- Helpers -------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))

def fetch_geojson(url):
    ctx = ssl.create_default_context()
    req = Request(url, headers={"User-Agent": "QuakeWatch/1.0"})
    with urlopen(req, timeout=10, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_client_ip_location():
    if "client_loc" in st.session_state:
        return st.session_state.client_loc
    components.html(
        """
        <script>
        async function getIP() {
            const resp = await fetch("https://api64.ipify.org?format=json");
            const data = await resp.json();
            window.parent.postMessage({type: "client_ip", value: data.ip}, "*");
        }
        getIP();
        </script>
        """,
        height=0,
    )
    return 14.5995, 120.9842, "Manila (fallback)"

def get_timezone(lat, lon):
    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lat=lat, lng=lon)
    if tz_name:
        return pytz.timezone(tz_name), tz_name
    return timezone.utc, "UTC"

# ------------------- Streamlit UI -------------------
st.set_page_config(page_title="🌍 Quake Watch", layout="wide")
st.title("🌍 Quake Watch - Real-time Earthquake Monitor")

# 🔄 Real-time refresh (user can pick interval)
refresh_rate = st.sidebar.slider("⏱️ Auto-refresh interval (seconds)", 30, 300, 60, 30)
st_autorefresh = st.sidebar.checkbox("Enable Auto-refresh", value=True)

if st_autorefresh:
    st_autorefresh_counter = st.experimental_autorefresh(interval=refresh_rate * 1000, limit=None, key="refresh_counter")
    st.caption(f"🔄 Auto-refresh every {refresh_rate} seconds")

# ------------------- Location Detection -------------------
if "client_ip" not in st.session_state:
    st.session_state.client_ip = None

client_ip = st.session_state.get("client_ip", None)
if client_ip and "client_loc" not in st.session_state:
    try:
        resp = requests.get(f"https://ipinfo.io/{client_ip}/json").json()
        if "loc" in resp:
            lat, lon = map(float, resp["loc"].split(","))
            city = resp.get("city", "")
            country = resp.get("country", "")
            label = ", ".join(x for x in [city, country] if x)
            st.session_state.client_loc = (lat, lon, label)
    except:
        st.session_state.client_loc = (14.5995, 120.9842, "Manila (fallback)")

st.sidebar.header("📍 Location Settings")
loc_mode = st.sidebar.radio("Choose location mode", ["Auto (Client IP)", "Select Country", "Manual Lat/Lon"])

if loc_mode == "Auto (Client IP)":
    user_lat, user_lon, user_label = get_client_ip_location()
    st.sidebar.success(f"Using your browser IP location: {user_label}")
elif loc_mode == "Select Country":
    countries = sorted([c.name for c in pycountry.countries])
    country = st.sidebar.selectbox("Choose a country", countries)
    geolocator = Nominatim(user_agent="quake_watch")
    try:
        loc = geolocator.geocode(country, timeout=10)
        if loc:
            user_lat, user_lon, user_label = loc.latitude, loc.longitude, country
        else:
            user_lat, user_lon, user_label = 14.5995, 120.9842, "Manila (fallback)"
    except:
        user_lat, user_lon, user_label = 14.5995, 120.9842, "Manila (fallback)"
elif loc_mode == "Manual Lat/Lon":
    user_lat = st.sidebar.number_input("Latitude", value=14.5995, format="%.4f")
    user_lon = st.sidebar.number_input("Longitude", value=120.9842, format="%.4f")
    user_label = f"Custom: {user_lat:.2f}, {user_lon:.2f}"

local_tz, tz_name = get_timezone(user_lat, user_lon)

# ------------------- Feed Selection -------------------
feed = st.selectbox("🌐 Select USGS Feed", list(USGS_FEEDS.keys()))
radius = st.slider("📏 Radius (km)", 50, 2000, 500, 50)
min_mag = st.slider("📊 Minimum Magnitude", 1.0, 8.0, 3.0, 0.5)

# ------------------- Fetch Events -------------------
data = fetch_geojson(USGS_FEEDS[feed])
events = []
for f in data["features"]:
    lon, lat = f["geometry"]["coordinates"][:2]
    mag = f["properties"]["mag"] or 0
    place = f["properties"]["place"]
    t_utc = datetime.utcfromtimestamp(f["properties"]["time"]/1000).replace(tzinfo=timezone.utc)
    t_disp = t_utc.astimezone(local_tz)
    dist = haversine_km(user_lat, user_lon, lat, lon)
    if mag >= min_mag and dist <= radius:
        events.append((t_disp, mag, place, lat, lon, dist))

events_sorted = sorted(events, key=lambda x: x[0], reverse=True)

# ------------------- Display -------------------
st.subheader(f"📝 Earthquake Events near {user_label}")

if events_sorted:
    df = pd.DataFrame([{
        "Time": e[0].strftime("%Y-%m-%d %H:%M:%S"),
        "Magnitude": e[1],
        "Place": e[2],
        "Lat": e[3],
        "Lon": e[4],
        "Dist (km)": e[5]
    } for e in events_sorted])

    st.dataframe(df, use_container_width=True, height=400)

    col1, col2 = st.columns([2,1])
    with col1:
        st.subheader("🗺️ Earthquake Map")
        m = folium.Map(location=[user_lat, user_lon], zoom_start=4, tiles="CartoDB positron")
        folium.Marker([user_lat, user_lon], tooltip=f"You: {user_label}", icon=folium.Icon(color="blue")).add_to(m)
        for e in events_sorted:
            color = "green" if e[1] < 4 else "orange" if e[1] < 6 else "red"
            folium.CircleMarker([e[3], e[4]], radius=4+e[1], color=color,
                                fill=True, fill_color=color, tooltip=f"M{e[1]:.1f} {e[2]}").add_to(m)
        st_folium(m, width=800, height=500)

    with col2:
        st.subheader("📈 Magnitude Trend")
        times = [e[0] for e in events_sorted]
        mags = [round(e[1],1) for e in events_sorted]
        fig, ax = plt.subplots(figsize=(6,4))
        ax.plot(times, mags, marker="o")
        ax.set_xlabel("Time")
        ax.set_ylabel("Magnitude")
        ax.grid(True)
        fig.autofmt_xdate()
        st.pyplot(fig)
else:
    st.info("No earthquake events found in this range.")
