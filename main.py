import streamlit as st
import requests, json, math, ssl, sqlite3
from datetime import datetime, timezone
from urllib.request import urlopen, Request
import folium
from streamlit_folium import st_folium
import pandas as pd
import pytz
import streamlit.components.v1 as components
import firebase_admin
from firebase_admin import credentials, messaging

# ------------------- CONFIG -------------------
USGS_FEEDS = {
    "Past Hour (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "Past Day (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "Past 7 Days (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
}

DB_FILE = "fcm_tokens.db"

# ------------------- INIT -------------------
# Initialize Firebase Admin SDK
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.warning(f"⚠ Firebase not initialized: {e}")

# Initialize SQLite
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY)")
conn.commit()

# ------------------- Helpers -------------------
def save_token(token: str):
    """Save FCM token into SQLite"""
    try:
        cur.execute("INSERT OR IGNORE INTO tokens (token) VALUES (?)", (token,))
        conn.commit()
    except Exception as e:
        print("DB Error:", e)

def load_tokens():
    """Load all FCM tokens"""
    cur.execute("SELECT token FROM tokens")
    return [row[0] for row in cur.fetchall()]

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

def send_quake_alert(token, event):
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=f"🚨 Earthquake M{event[1]:.1f}",
                body=f"Near {event[2]} at {event[0].strftime('%Y-%m-%d %H:%M:%S UTC')}"
            ),
            token=token,
        )
        return messaging.send(message)
    except Exception as e:
        return str(e)

# ------------------- Streamlit UI -------------------
st.set_page_config(page_title="🌍 Quake Watch FCM", layout="wide")
st.title("🌍 Quake Watch + Firebase Notifications")

# Sidebar filters
feed = st.sidebar.selectbox("🌐 Select USGS Feed", list(USGS_FEEDS.keys()))
radius = st.sidebar.slider("📏 Radius (km)", 50, 2000, 500, 50)
min_mag = st.sidebar.slider("📊 Minimum Magnitude", 1.0, 8.0, 3.0, 0.5)
alert_mag = st.sidebar.slider("🚨 Notify if magnitude ≥", 4.0, 8.0, 6.0, 0.5)

# ------------------- Inject Firebase JS -------------------
components.html(f"""
<script src="https://www.gstatic.com/firebasejs/9.6.1/firebase-app.js"></script>
<script src="https://www.gstatic.com/firebasejs/9.6.1/firebase-messaging.js"></script>
<script>
  const firebaseConfig = {{
    apiKey: "AIzaSyB3uk0a4RSU9EcOJLadaWYvX_v8O82YWbs",
    authDomain: "earthquakewatch-1f530.firebaseapp.com",
    projectId: "earthquakewatch-1f530",
    storageBucket: "earthquakewatch-1f530.firebasestorage.app",
    messagingSenderId: "550569254609",
    appId: "1:550569254609:web:4b4ece5b41b577f7f0eff0",
    measurementId: "G-ZYPH4R72KE"
  }};
  const app = firebase.initializeApp(firebaseConfig);
  const messaging = firebase.messaging();

  Notification.requestPermission().then((permission) => {{
    if (permission === "granted") {{
      messaging.getToken({{ vapidKey: "YOUR_REAL_PUBLIC_VAPID_KEY" }}).then((currentToken) => {{
        if (currentToken) {{
          window.parent.postMessage({{ type: "fcm_token", value: currentToken }}, "*");
        }}
      }});
    }}
  }});
</script>
""", height=0)

# ------------------- Capture Token -------------------
if "fcm_token" in st.session_state:
    save_token(st.session_state.fcm_token)
tokens = load_tokens()
if tokens:
    st.success(f"✅ {len(tokens)} devices subscribed for push alerts")

# ------------------- Fetch Earthquake Data -------------------
data = fetch_geojson(USGS_FEEDS[feed])
events = []
for f in data["features"]:
    lon, lat = f["geometry"]["coordinates"][:2]
    mag = f["properties"]["mag"] or 0
    place = f["properties"]["place"]
    t_utc = datetime.utcfromtimestamp(f["properties"]["time"]/1000).replace(tzinfo=timezone.utc)
    dist = haversine_km(14.5995, 120.9842, lat, lon)  # default: Manila
    if mag >= min_mag and dist <= radius:
        events.append((t_utc, mag, place, lat, lon, dist))

events_sorted = sorted(events, key=lambda x: x[0], reverse=True)

# ------------------- Display Data -------------------
if events_sorted:
    st.subheader("📝 Recent Earthquake Events")
    df = pd.DataFrame([{
        "Time (UTC)": e[0].strftime("%Y-%m-%d %H:%M:%S"),
        "Magnitude": e[1],
        "Place": e[2],
        "Lat": e[3],
        "Lon": e[4],
        "Dist (km)": e[5]
    } for e in events_sorted])
    st.dataframe(df, use_container_width=True, height=400)

    # Map
    m = folium.Map(location=[14.5995, 120.9842], zoom_start=4, tiles="CartoDB positron")
    for e in events_sorted:
        color = "green" if e[1] < 4 else "orange" if e[1] < 6 else "red"
        folium.CircleMarker(
            [e[3], e[4]],
            radius=4 + e[1],
            color=color,
            fill=True,
            fill_color=color,
            tooltip=f"M{e[1]:.1f} {e[2]}"
        ).add_to(m)
    st_folium(m, width=800, height=500)

    # ✅ Send Alerts if threshold exceeded
    if events_sorted[0][1] >= alert_mag and tokens:
        st.warning(f"🚨 Sending FCM alerts for M{events_sorted[0][1]:.1f} quake...")
        for token in tokens:
            result = send_quake_alert(token, events_sorted[0])
            st.text(f"Token: {token[:20]}... → {result}")
else:
    st.info("No recent earthquakes in range.")
