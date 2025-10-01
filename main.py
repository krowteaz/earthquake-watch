import streamlit as st
import requests, json, math, ssl
from datetime import datetime, timezone
from urllib.request import urlopen, Request
import pycountry
import folium
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
from tzlocal import get_localzone   # local timezone

# USGS feeds
USGS_FEEDS = {
    "Past Hour (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "Past Day (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "Past 7 Days (M1.0+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_week.geojson",
    "Past 7 Days (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
    "Past 7 Days (M4.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
}

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

# ✅ Get location from IP
def get_ip_location():
    try:
        data = requests.get("https://ipinfo.io/json", timeout=10).json()
        if "loc" in data:
            lat, lon = map(float, data["loc"].split(","))
            city = data.get("city","")
            country = data.get("country","")
            label = ", ".join(x for x in [city,country] if x)
            return lat, lon, label
    except:
        pass
    return 14.5995, 120.9842, "Manila, PH"

# ---------------- Streamlit UI ----------------
st.set_page_config(page_title="🌍 Quake Watch", layout="wide")
st.title("🌍 Quake Watch - Earthquake Monitor (Web Edition)")

# Location options
st.sidebar.header("📍 Location Settings")
loc_mode = st.sidebar.radio("Choose location mode", ["Auto (IP)", "Select Country", "Manual Lat/Lon"])

if loc_mode == "Auto (IP)":
    user_lat, user_lon, user_label = get_ip_location()
    st.sidebar.success(f"Using your IP location: {user_label}")
elif loc_mode == "Select Country":
    countries = sorted([c.name for c in pycountry.countries])
    country = st.sidebar.selectbox("Choose a country", countries)
    # use geopy here if needed, for now fallback to approximate center via Nominatim
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="quake_watch")
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

# Feed & filters
feed = st.selectbox("🌐 Select USGS Feed", list(USGS_FEEDS.keys()))
radius = st.slider("📏 Radius (km)", 50, 2000, 500, 50)
min_mag = st.slider("📊 Minimum Magnitude", 1.0, 8.0, 3.0, 0.5)
time_mode = st.radio("🕒 Show Time As", ["Local Time", "UTC"])

LOCAL_TZ = get_localzone()

# Fetch events
data = fetch_geojson(USGS_FEEDS[feed])
events = []
for f in data["features"]:
    lon, lat = f["geometry"]["coordinates"][:2]
    mag = f["properties"]["mag"] or 0
    place = f["properties"]["place"]
    t_utc = datetime.utcfromtimestamp(f["properties"]["time"]/1000).replace(tzinfo=timezone.utc)
    if time_mode == "Local Time":
        t_disp = t_utc.astimezone(LOCAL_TZ)
    else:
        t_disp = t_utc
    dist = haversine_km(user_lat, user_lon, lat, lon)
    if mag >= min_mag and dist <= radius:
        events.append((t_disp, mag, place, lat, lon, dist))

# Events table
st.subheader(f"📝 Earthquake Events near {user_label}")
st.dataframe([{
    "Time": e[0].strftime("%Y-%m-%d %H:%M:%S"),
    "Magnitude": e[1],
    "Place": e[2],
    "Lat": e[3],
    "Lon": e[4],
    "Dist (km)": f"{e[5]:.1f}"
} for e in events])

# Layout: Map & Chart
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("🗺️ Earthquake Map")
    m = folium.Map(location=[user_lat, user_lon], zoom_start=4, tiles="CartoDB positron")
    folium.Marker([user_lat, user_lon], tooltip=f"You: {user_label}", icon=folium.Icon(color="blue")).add_to(m)
    for e in events:
        color = "green" if e[1] < 4 else "orange" if e[1] < 6 else "red"
        folium.CircleMarker(
            [e[3], e[4]],
            radius=4 + e[1],
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            tooltip=f"M{e[1]} {e[2]} at {e[0].strftime('%H:%M:%S')}"
        ).add_to(m)
    st_folium(m, width=800, height=500)

with col2:
    st.subheader("📈 Magnitude Trend")
    if events:
        events_sorted = sorted(events, key=lambda x: x[0])
        times = [e[0] for e in events_sorted]
        mags = [e[1] for e in events_sorted]
        colors = ["green" if m < 4 else "orange" if m < 6 else "red" for m in mags]

        fig, ax = plt.subplots(figsize=(6,4))
        ax.scatter(times, mags, c=colors, s=[20+m*5 for m in mags], alpha=0.8)
        ax.plot(times, mags, color="gray", linewidth=0.8, linestyle="--")
        ax.set_xlabel("Time")
        ax.set_ylabel("Magnitude")
        ax.grid(True, linestyle=":", linewidth=0.6)
        fig.autofmt_xdate()
        st.pyplot(fig)
    else:
        st.info("No events to plot.")
