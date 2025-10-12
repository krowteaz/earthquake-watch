
import streamlit as st
import pandas as pd
import pytz
import folium
from folium.plugins import MousePosition, MarkerCluster, HeatMap
from streamlit_folium import st_folium
from datetime import datetime, timezone, timedelta

# Optional dependency for precise timezone
try:
    from timezonefinder import TimezoneFinder  # type: ignore
    _TZF_AVAILABLE = True
except Exception:
    TimezoneFinder = None  # type: ignore
    _TZF_AVAILABLE = False

from utils.geo import center_for_region, get_timezone_for, filter_df_by_region, suggest_region_from_coords
from utils.helpers import (
    USGS_FEEDS, fetch_usgs_feed, format_quake_row, safe_float, km_distance,
    magnitude_color, distance_bucket, country_flag_from_place, country_code_from_place,
    flag_url_from_code, moving_average, unique_quake_id, ip_geolocate, fetch_quake_news
)
from utils.forecast import energy_release, trend_indicator

st.set_page_config(page_title="ALOG Safety App • Real-Time Safety", page_icon="🛡️", layout="wide")

# -------------------- Theme & CSS --------------------
with open("assets/style.css", "r", encoding="utf-8") as f:
    base_css = f.read()

st.sidebar.subheader("🎨 Brand Theme")
theme = st.sidebar.selectbox("Mode", ["ALOG Dark", "ALOG Light"], index=0)
accent_name = st.sidebar.selectbox("Accent", ["Teal", "Orange", "Blue", "Emerald"], index=0)
accent_map = {"Teal":"#00d3b7", "Orange":"#ff8a00", "Blue":"#3b82f6", "Emerald":"#10b981"}
ACCENT = accent_map[accent_name]

if theme == "ALOG Dark":
    BG = "#0b1116"; CARD = "rgba(255,255,255,0.04)"; TEXT="#e5eef5"; MUTED="#86a2b4"; BORDER="rgba(255,255,255,0.09)"
else:
    BG = "#f7fbff"; CARD = "#ffffff"; TEXT="#0b1116"; MUTED="#6b7b8c"; BORDER="#e6eef3"

theme_css = f"""
<style>
:root{{
  --alog-accent:{ACCENT};
  --alog-card:{CARD};
  --alog-border:{BORDER};
  --alog-muted:{MUTED};
}}
html, body, [data-testid="stAppViewContainer"] {{
  color: {TEXT} !important; background: {BG} !important;
}}
</style>
"""
st.markdown(theme_css + f"<style>{base_css}</style>", unsafe_allow_html=True)

# -------------------- Sidebar Controls --------------------
st.sidebar.title("⚙️ Controls")

# Session state
if "region_value" not in st.session_state:
    st.session_state["region_value"] = "Worldwide"
if "manual_pin" not in st.session_state:
    st.session_state["manual_pin"] = None  # (lat, lon)
if "attempted_ip_auto" not in st.session_state:
    st.session_state["attempted_ip_auto"] = False
if "ip_coords" not in st.session_state:
    st.session_state["ip_coords"] = None  # (lat, lon)

# Location controls
st.sidebar.subheader("📍 Location (for distance reference)")
st.sidebar.caption("Distance is computed from **Manual Pin** if set; otherwise from **IP**; otherwise from current coordinates.")
gps_btn = st.sidebar.button("Use my location (GPS)")
ip_btn = st.sidebar.button("Use my IP location")
auto_match = st.sidebar.toggle("🔄 Auto-match Region", value=True, help="Switch Region based on GPS/IP/Pin.")
flag_provider = st.sidebar.selectbox("Flag images via", ["Flagpedia (default)", "FlagCDN"], index=0)

# Map overlays
cluster_on = st.sidebar.toggle("🧩 Cluster markers", value=True)
heatmap_on = st.sidebar.toggle("🔥 Density HeatMap (map)", value=True)
heat_radius = st.sidebar.slider("HeatMap radius", 8, 30, 14, 1)

dense_rows = st.sidebar.toggle("Dense rows (table)", value=True)
table_height_mode = st.sidebar.selectbox("Table height", ["Auto-fit 10 rows", "Tall (600px)", "Compact (480px)"], index=0)

# Query params lat/lon/src
q = st.query_params
lat_val = q.get("lat"); lon_val = q.get("lon"); src_param = q.get("src")
lat = safe_float(lat_val[0] if isinstance(lat_val, list) else lat_val)
lon = safe_float(lon_val[0] if isinstance(lon_val, list) else lon_val)
src_param = (src_param[0] if isinstance(src_param, list) else src_param) or None
location_source = None

# Inject geolocation JS when GPS pressed
if gps_btn:
    st.components.v1.html("""
    <script>
      (function(){
        if (!navigator.geolocation) { alert("Geolocation unsupported."); return; }
        navigator.geolocation.getCurrentPosition(function(pos){
          const url = new URL(window.location.href);
          url.searchParams.set('lat', pos.coords.latitude.toFixed(6));
          url.searchParams.set('lon', pos.coords.longitude.toFixed(6));
          url.searchParams.set('src', 'gps');
          window.location.href = url.toString();
        }, function(err){ alert("Location error: "+ err.message); }, {enableHighAccuracy:true, timeout:10000, maximumAge:0});
      })();
    </script>
    """, height=0, width=0)

# IP geolocation on demand
ip_meta = None
if (lat is None or lon is None) and ip_btn:
    ip_lat, ip_lon, label, meta = ip_geolocate()
    if ip_lat is not None and ip_lon is not None:
        lat, lon, location_source, ip_meta = ip_lat, ip_lon, "IP", meta
        st.session_state["ip_coords"] = (ip_lat, ip_lon)
        st.query_params.update({"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "src": "ip"})
    else:
        st.sidebar.error("Could not determine location from IP.")

# If still no coords, try automatic IP fallback once per session
if (lat is None or lon is None) and not st.session_state["attempted_ip_auto"]:
    ip_lat, ip_lon, label, meta = ip_geolocate()
    st.session_state["attempted_ip_auto"] = True
    if ip_lat is not None and ip_lon is not None:
        lat, lon, location_source, ip_meta = ip_lat, ip_lon, "IP", meta
        st.session_state["ip_coords"] = (ip_lat, ip_lon)

user_has_coords = (lat is not None and lon is not None)
# Determine source
if src_param == "gps": location_source = "GPS"
elif src_param == "pin": location_source = "Manual Pin"
elif src_param == "ip": location_source = "IP"
elif location_source is None and user_has_coords: location_source = "GPS/Manual"

# Auto-match region if coords
if user_has_coords and auto_match:
    suggested = suggest_region_from_coords(lat, lon)
    if suggested and suggested != st.session_state["region_value"]:
        st.session_state["region_value"] = suggested

# Region select
REGION_OPTIONS = ["Worldwide", "Philippines", "Japan", "Indonesia", "Taiwan", "USA West", "Mexico", "Chile", "New Zealand", "Fiji"]
region_index = REGION_OPTIONS.index(st.session_state["region_value"]) if st.session_state["region_value"] in REGION_OPTIONS else 0
region_widget_value = st.sidebar.selectbox("Region filter", options=REGION_OPTIONS, index=region_index, key="region_select")
if region_widget_value != st.session_state["region_value"]:
    st.session_state["region_value"] = region_widget_value
region_effective = st.session_state["region_value"]

# Resolve timezone
if _TZF_AVAILABLE and user_has_coords:
    tz_name = get_timezone_for(lat, lon)
elif _TZF_AVAILABLE:
    cen = center_for_region(region_effective); tz_name = get_timezone_for(cen[0], cen[1])
else:
    tz_name = "Asia/Manila"
local_tz = pytz.timezone(tz_name)

# Header + Status Badge
st.title("🛡️ ALOG — Adaptive Local Quake Alert (Real-Time Safety) v10.8.1")
st.caption("*Emphasizes localized real-time alerts and adaptive updates during seismic activity.*")

ip_text = ""
if (location_source or "") == "IP" and ip_meta:
    parts = []
    if ip_meta.get("ip"): parts.append(ip_meta["ip"])
    locbits = [ip_meta.get("city"), ip_meta.get("region"), ip_meta.get("country")]
    locbits = [b for b in locbits if b]
    if locbits: parts.append(", ".join(locbits))
    if ip_meta.get("provider"): parts.append(ip_meta["provider"])
    ip_text = " — " + " • ".join(parts)

if user_has_coords:
    if location_source == "GPS":
        status_label = "GPS"; status_class = "pill gps"
    elif location_source == "IP":
        status_label = "IP" + ip_text; status_class = "pill ip"
    elif "Manual" in (location_source or ""):
        status_label = "Manual Pin"; status_class = "pill manual"
    else:
        status_label = "Coords set"; status_class = "pill neutral"
else:
    status_label = "No location set"; status_class = "pill none"
st.markdown(f"<div class='statusbar'>Location: <span class='{status_class}'>{status_label}</span> • Region: <b>{region_effective}</b> • TZ: <code>{tz_name}</code></div>", unsafe_allow_html=True)

st.info("**Distance (km)** is computed from **Manual Pin → IP → GPS** (in that order). Click the map to set a Manual Pin.")

# -------------------- Other Controls --------------------
feed_name = st.sidebar.selectbox("USGS Feed", list(USGS_FEEDS.keys()), index=1)
col_a, col_b = st.sidebar.columns(2)
min_mag = col_a.slider("Min Mag", 0.0, 9.9, 2.5, 0.1)
notify_mag = col_b.slider("Notify ≥", 0.0, 9.9, 5.0, 0.1)

# Auto Refresh
st.sidebar.subheader("⏱️ Auto Refresh")
refresh_map = {"Off": 0, "1 minute": 60, "5 minutes": 300, "15 minutes": 900, "30 minutes": 1800, "1 hour": 3600}
default_index = list(refresh_map.keys()).index("1 minute")
refresh_choice = st.sidebar.selectbox("Refresh every…", list(refresh_map.keys()), index=default_index)
refresh_seconds = refresh_map[refresh_choice]
if refresh_seconds > 0:
    from streamlit_autorefresh import st_autorefresh
    count = st_autorefresh(interval=refresh_seconds * 1000, limit=None, key="alog_auto_refresh")
    st.sidebar.caption(f"Auto refresh: every {refresh_seconds}s (refresh #{count})")
else:
    st.sidebar.caption("Auto refresh is OFF")

# -------------------- Fetch Data --------------------
with st.spinner("Fetching latest earthquakes..."):
    data = fetch_usgs_feed(USGS_FEEDS[feed_name])

rows = []
for feat in data.get("features", []):
    row = format_quake_row(feat, local_tz)
    if row is None: continue
    rows.append(row)

df_raw = pd.DataFrame(rows)
if df_raw.empty:
    st.warning("No data received from the selected USGS feed.")
    st.stop()

# Compute local times
if "time_utc" in df_raw.columns:
    def to_local_str(dt_naive_utc):
        try:
            aware_utc = pytz.utc.localize(dt_naive_utc)
            local_dt = aware_utc.astimezone(local_tz)
            return local_dt, local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return None, ""
    conv = df_raw["time_utc"].apply(to_local_str)
    df_raw["time_local"] = [c[0] for c in conv]
    df_raw["time_local_str"] = [c[1] for c in conv]

# Region filter
df = filter_df_by_region(df_raw, region_effective)

# -------------------- Choose distance reference (Pin → IP → GPS/coords) --------------------
ref_source = "None"
ref_lat = ref_lon = None
if st.session_state.get("manual_pin"):
    ref_lat, ref_lon = st.session_state["manual_pin"]
    ref_source = "Manual Pin"
elif st.session_state.get("ip_coords"):
    ref_lat, ref_lon = st.session_state["ip_coords"]
    ref_source = "IP"
elif (lat is not None and lon is not None):
    ref_lat, ref_lon = lat, lon
    ref_source = "GPS/Coords"

# Distance & flags
if ref_lat is not None and ref_lon is not None:
    df["distance_km"] = df.apply(lambda r: km_distance(ref_lat, ref_lon, r["lat"], r["lon"]), axis=1)
else:
    df["distance_km"] = None

df["flag_emoji"] = df["place"].apply(country_flag_from_place).astype(str)
df["flag_code"] = df["place"].apply(country_code_from_place).astype(str)

provider = "flagcdn" if flag_provider.startswith("FlagCDN") else "flagpedia"
df["flag_img"] = df["flag_code"].apply(lambda c: flag_url_from_code(c, provider=provider) if c and c != "None" else None)

# Distance label/color/icon
df[["dist_label","dist_color","dist_icon"]] = df.apply(lambda r: pd.Series(distance_bucket(r.get("distance_km"))), axis=1)

# Filter by min mag and sort by time desc
df = df[df["mag"].fillna(0) >= min_mag].sort_values("time_utc", ascending=False).reset_index(drop=True)

if df.empty:
    st.info(f"No events in **{region_effective}** with the current filters. Try lowering 'Min Mag' or choose a different feed.")
    st.stop()

# -------------------- Notification --------------------
def notify_js(title, body, play_sound=False):
    audio_tag = f"""var a=new Audio('assets/alert.wav');a.play().catch(()=>{{}});""" if play_sound else ""
    return f"""
    <script>
    (function(){{
      try {{
        if (!("Notification" in window)) return;
        function doNotify(){{ new Notification("{title}", {{ body: "{body}" }}); }}
        if (Notification.permission === "granted") {{ doNotify(); {audio_tag} }}
        else if (Notification.permission !== "denied") {{
          Notification.requestPermission().then(function(p){{ if(p==="granted"){{ doNotify(); {audio_tag} }} }});
        }}
      }} catch(e) {{}}
    }})();
    </script>
    """

if "last_seen_quake" not in st.session_state: st.session_state["last_seen_quake"] = None
top = df.iloc[0] if not df.empty else None
if top is not None and top["mag"] >= notify_mag:
    label, _, icon = distance_bucket(top.get("distance_km"))
    play_sound = (label == "Local Critical") and (top["mag"] >= 5.0)
    eid = unique_quake_id(top)
    if st.session_state["last_seen_quake"] != eid:
        st.session_state["last_seen_quake"] = eid
        notif_title = f"{icon} {top['flag_emoji']} M{top['mag']} • {top['place'][:24]}"
        local_str = top.get("time_local").strftime("%Y-%m-%d %H:%M:%S %Z") if top.get("time_local") else top.get("time_local_str","")
        st.components.v1.html(notify_js(notif_title, f"{local_str} • {label}", play_sound=play_sound), height=0, width=0)

# -------------------- Tabs --------------------
tab_live, tab_forecast, tab_news = st.tabs(["🌐 Live Alerts", "📊 Forecast & Trends", "📰 News"])

with tab_live:
    # ---- MAP (TOP) ----
    center = center_for_region(region_effective) if region_effective!="Worldwide" else (0,0)
    m = folium.Map(location=center, zoom_start=5 if region_effective!="Worldwide" else 2, control_scale=True)
    st.caption("🖱️ Click anywhere on the map to set a Manual Pin.")

    # Show the chosen distance reference on map
    if ref_source == "GPS/Coords" and (lat is not None and lon is not None):
        folium.Marker(location=(lat, lon), tooltip=f"Your location (GPS/Coords)", icon=folium.Icon(icon="user")).add_to(m)
    if ref_source == "IP" and st.session_state.get("ip_coords"):
        ip_lat, ip_lon = st.session_state["ip_coords"]
        folium.Marker(location=(ip_lat, ip_lon), tooltip=f"IP location", icon=folium.Icon(color="green", icon="info-sign")).add_to(m)
    if ref_source == "Manual Pin" and st.session_state.get("manual_pin"):
        pin_lat, pin_lon = st.session_state["manual_pin"]
        folium.Marker(location=(pin_lat, pin_lon), tooltip="Manual Pin", icon=folium.Icon(color="blue", icon="map-marker")).add_to(m)

    cluster_layer = MarkerCluster(name="Quakes") if cluster_on else None
    bounds = []
    heat_points = []

    for _, r in df.iterrows():
        lat_q, lon_q = float(r["lat"]), float(r["lon"])
        mag = float(r["mag"]) if pd.notna(r["mag"]) else 0.0
        depth = r.get("depth_km")
        mag_fill = magnitude_color(mag)
        stroke_color = r.get("dist_color") or "#000000"
        radius = max(4, min(16, 3 + mag * 2))

        local_str = r.get("time_local_str") or ""
        dist_km = r.get("distance_km")
        dist_str = f"{dist_km:.0f} km" if dist_km is not None else "—"

        popup_html = f"""
        <div style='font-size:12px'>
          <b>{r.get('flag_emoji','')} M{mag:.1f}</b> — {r.get('place','')}<br/>
          <b>Local:</b> {local_str}<br/>
          <b>Depth:</b> {depth} km • <b>Dist:</b> {dist_str}<br/>
          <b>Lat/Lon:</b> {lat_q:.3f}, {lon_q:.3f}
        </div>
        """

        marker = folium.CircleMarker(
            location=(lat_q, lon_q),
            radius=radius, color=stroke_color, weight=3,
            fill=True, fill_color=mag_fill, fill_opacity=0.8,
            tooltip=f"M{mag:.1f} • {r.get('place','')}",
            popup=folium.Popup(popup_html, max_width=320)
        )
        if cluster_layer:
            cluster_layer.add_child(marker)
        else:
            marker.add_to(m)
        bounds.append((lat_q, lon_q))
        heat_points.append([lat_q, lon_q, max(0.5, mag - 1.5)])

    if cluster_layer:
        cluster_layer.add_to(m)

    if heatmap_on and heat_points:
        HeatMap(heat_points, radius=heat_radius, blur=int(heat_radius*0.9), min_opacity=0.2, max_zoom=7).add_to(m)

    MousePosition(position="bottomright", separator=" | ", prefix="Lat/Lon").add_to(m)
    if bounds:
        try: m.fit_bounds(bounds, padding=(20,20))
        except Exception: pass

    st_data = st_folium(m, height=560, use_container_width=True, key="live_map", returned_objects=['last_clicked'])
    if st_data and st_data.get("last_clicked"):
        pin_lat = float(st_data["last_clicked"]["lat"]); pin_lon = float(st_data["last_clicked"]["lng"])
        st.session_state["manual_pin"] = (pin_lat, pin_lon)
        st.query_params.update({"lat": f"{pin_lat:.6f}", "lon": f"{pin_lon:.6f}", "src": "pin"})
        st.success(f"Manual pin set at: {pin_lat:.4f}, {pin_lon:.4f}")
        if auto_match:
            suggested = suggest_region_from_coords(pin_lat, pin_lon)
            if suggested and suggested != st.session_state["region_value"]:
                st.session_state["region_value"] = suggested
        st.rerun()

    # ---- LIVE EVENTS (BOTTOM) ----
    st.subheader("Events & Tally")
    ref_txt = f"{ref_source} @ {ref_lat:.2f},{ref_lon:.2f}" if ref_lat is not None else "None"
    st.caption(f"Region: **{region_effective}** • Feed: {feed_name} • TZ: {tz_name} • Distance ref: {ref_txt} • Showing {len(df)} events (M ≥ {min_mag})")

    # Explain computation (short note + optional math)
    st.caption("**Dist (km)** uses the great-circle *haversine* formula with Earth radius **R = 6371 km** from your reference point to each event.")
    with st.expander("Show distance computation details"):
        st.markdown(f"""
We compute the distance for each event from **{ref_source}** at **({ref_lat}, {ref_lon})** using the haversine formula:

\\[
d = 2R \\arcsin\\left( \\sqrt{{\\sin^2\\left(\\frac{{\\Delta\\varphi}}{2}\\right) + \\cos(\\varphi_1)\\cos(\\varphi_2)\\sin^2\\left(\\frac{{\\Delta\\lambda}}{2}\\right)}} \\right),\\quad R=6371\\,\\text{{km}}
\\]

where \\(\\varphi\\) is latitude (radians) and \\(\\lambda\\) is longitude.
        """)

    now_local = datetime.now(local_tz)
    total = len(df); majors = int((df["mag"] >= 6.0).sum())
    l_crit = int((df["dist_label"] == "Local Critical").sum())
    l_watch = int((df["dist_label"] == "Regional Watch").sum())
    l_ext = int((df["dist_label"] == "Extended Zone").sum())
    l_far = int((df["dist_label"] == "Distant Info").sum())
    valid_emojis = df.loc[df["flag_emoji"].notna(), "flag_emoji"]
    top_flag_emoji = valid_emojis.mode().iloc[0] if not valid_emojis.empty else "🏳️"

    s1, c1, c2, c3, c4, s2 = st.columns([0.1, 1, 1, 1, 1, 0.1])
    c1.metric("🌍 Total", total)
    c2.metric("⚡ Major (≥6.0)", majors)
    c3.metric("Top Region", str(top_flag_emoji))
    c4.metric("Last Refresh", now_local.strftime("%Y-%m-%d %H:%M:%S"))

    s3, c5, c6, c7, c8, s4 = st.columns([0.1, 1, 1, 1, 1, 0.1])
    c5.metric("🔴 ≤300", l_crit); c6.metric("🟡 301–600", l_watch)
    c7.metric("🔵 601–1000", l_ext); c8.metric("🟢 >1000", l_far)

    # Live Events table
    st.markdown("#### Live Events (10 per page)")
    df_table = df.copy().reset_index(drop=True)
    df_table["Flag"] = df_table["flag_img"]
    df_table["Region"] = df_table["flag_emoji"].astype(str) + " " + df_table["place"].astype(str)
    df_table = df_table[[
        "Flag","Region","time_local_str","mag","depth_km","lat","lon","distance_km","dist_label"
    ]].rename(columns={
        "time_local_str":"Local Time","mag":"Mag","depth_km":"Depth (km)","lat":"Lat","lon":"Lon",
        "distance_km":"Dist (km)","dist_label":"Distance Class"
    })
    df_table["Mag"] = df_table["Mag"].map(lambda x: round(float(x),1) if pd.notna(x) else x)
    if df_table["Dist (km)"].notna().any():
        df_table["Dist (km)"] = df_table["Dist (km)"].map(lambda x: round(x) if pd.notna(x) else x)

    page_size = 10
    total_pages = max(1, (len(df_table) + page_size - 1) // page_size)
    page_num = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1, key="live_table_page")
    start = (page_num - 1) * page_size
    end_idx = start + page_size
    shown = df_table.iloc[start:end_idx]
    st.caption(f"Showing {start+1}–{min(end_idx, len(df_table))} of {len(df_table)} events")

    header_px = 52 if dense_rows else 56
    row_px = 36 if dense_rows else 44
    if table_height_mode == "Auto-fit 10 rows":
        height = max(300, min(780, header_px + row_px * len(shown)))
    elif table_height_mode == "Tall (600px)":
        height = 600
    else:
        height = 480

    if dense_rows:
        st.markdown("<div class='dense-table'>", unsafe_allow_html=True)
    st.dataframe(
        shown,
        use_container_width=True,
        height=height,
        hide_index=True,
        column_config={
            "Flag": st.column_config.ImageColumn("Flag", help=f"Country flag via {flag_provider.split()[0].lower()}.", width="small"),
            "Region": st.column_config.TextColumn("Region"),
            "Local Time": st.column_config.TextColumn("Local Time"),
            "Mag": st.column_config.NumberColumn("Mag"),
            "Depth (km)": st.column_config.NumberColumn("Depth (km)"),
            "Lat": st.column_config.NumberColumn("Lat"),
            "Lon": st.column_config.NumberColumn("Lon"),
            "Dist (km)": st.column_config.NumberColumn("Dist (km)"),
            "Distance Class": st.column_config.TextColumn("Distance Class"),
        }
    )
    if dense_rows:
        st.markdown("</div>", unsafe_allow_html=True)

    # Heatmap chart
    st.markdown("#### Heatmap (Time of Day × Magnitude)")
    try:
        df_hm = df.copy()
        df_hm = df_hm[pd.notna(df_hm["time_local"]) & pd.notna(df_hm["mag"])]
        if not df_hm.empty:
            df_hm["hour"] = df_hm["time_local"].apply(lambda t: t.hour if t else None)
            def mag_band(m):
                if m < 3: return "<3"
                if m < 4: return "3–3.9"
                if m < 5: return "4–4.9"
                if m < 6: return "5–5.9"
                if m < 7: return "6–6.9"
                return "≥7"
            df_hm["band"] = df_hm["mag"].astype(float).apply(mag_band)
            bands = ["<3","3–3.9","4–4.9","5–5.9","6–6.9","≥7"]
            import numpy as np, matplotlib.pyplot as plt
            mat = np.zeros((len(bands), 24), dtype=int)
            for _, rr in df_hm.iterrows():
                mat[bands.index(rr["band"]), int(rr["hour"])] += 1
            fig, ax = plt.subplots(figsize=(8.5, 2.8), dpi=120)
            im = ax.imshow(mat, aspect="auto", origin="lower")
            ax.set_yticks(range(len(bands))); ax.set_yticklabels(bands)
            ax.set_xticks(range(0,24,2)); ax.set_xticklabels([str(h) for h in range(0,24,2)])
            ax.set_xlabel("Hour of day (local)"); ax.set_title("Activity Heatmap")
            cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02); cbar.ax.set_ylabel("Count", rotation=90)
            st.pyplot(fig, use_container_width=True)
        else:
            st.caption("Not enough data to render heatmap chart.")
    except Exception as e:
        st.caption(f"Heatmap chart unavailable: {e}")

with tab_forecast:
    st.subheader("Forecast & Trends (Heuristic)")
    st.caption("We analyze the filtered events only. This is **not** a predictive model; it's a descriptive activity indicator.")

    with st.expander("What does the Forecast Status mean?"):
        st.markdown("""
**How we compute it (simple heuristic):**  
1) We compute a **moving average** of magnitudes (recent window).  
2) We compare **recent average** vs **overall average**.  
3) We count **major quakes (≥6.0)** in the filtered dataset.  

**Status levels:**  
- **Stable** — Recent average ≤ 5% above overall, and **< 2** major quakes.  
- **Elevated** — Recent average > 5% above overall **or** ≥ 2 majors.  
- **High** — Recent average > 15% above overall **or** ≥ 3 majors.  

These thresholds are conservative and intended for situational awareness only.
        """)

    if len(df) >= 3:
        df_asc = df.sort_values("time_utc").reset_index(drop=True)
        mags = df_asc["mag"].astype(float).tolist()
        ma = moving_average(mags, window=min(7, max(3, len(mags)//4)))
        total_energy = energy_release(mags)
        indicator, reason = trend_indicator(df_asc)

        c1, c2, c3 = st.columns(3)
        c1.metric("Events", len(df)); c2.metric("Energy Index (rel.)", f"{total_energy:,.0f}"); c3.metric("Activity", indicator)
        st.caption(reason)

        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6.0, 3.0), dpi=120)
        ax.plot(range(len(mags)), mags, linewidth=1.8, marker='o', markersize=3, label="Magnitude")
        ax.plot(range(len(ma)), ma, linewidth=2.2, marker=None, alpha=0.9, label="Moving Avg")
        ax.set_xlabel("Event order (old → recent)", fontsize=10)
        ax.set_ylabel("Magnitude", fontsize=10)
        ax.set_title("Magnitude & Moving Average", fontsize=11)
        ax.grid(True, alpha=0.3); ax.tick_params(labelsize=9); ax.margins(x=0.02); ax.legend(fontsize=9, loc="best")
        st.pyplot(fig, use_container_width=False)

        st.markdown("##### Recent Window Summaries")
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        def count_in_days(days):
            cutoff = now_utc - timedelta(days=days)
            return int((df["time_utc"] >= cutoff).sum())
        colx, coly, colz = st.columns(3)
        colx.metric("Last 24h", count_in_days(1))
        coly.metric("Last 7 days", count_in_days(7))
        colz.metric("Last 30 days", count_in_days(30))
    else:
        st.info("Not enough data for meaningful trends. Try selecting a longer USGS feed.")

with tab_news:
    st.subheader("📰 Earthquake News")
    st.caption("Latest headlines for your **selected region**. We try to fetch images when feeds don't include them.")
    only_imgs = st.checkbox("Show only stories with images", value=False)
    with st.spinner("Fetching news..."):
        news_items = fetch_quake_news(st.session_state["region_value"], limit=12)

    if only_imgs:
        news_items = [n for n in news_items if n.get("image")]

    if not news_items:
        st.info("No news found right now. Try a different region or check again later.")
    else:
        for item in news_items:
            with st.container(border=True):
                cols = st.columns([1, 3])
                if item.get("image"):
                    cols[0].image(item["image"], use_column_width=True)
                else:
                    cols[0].markdown("🖼️ *(no image)*")
                title_md = f"**[{item['title']}]({item['link']})**" if item.get("link") else f"**{item['title']}**"
                cols[1].markdown(title_md)
                meta = []
                if item.get("source"): meta.append(item["source"])
                if item.get("published"): meta.append(item["published"])
                if meta:
                    cols[1].caption(" • ".join(meta))
                if item.get("summary"):
                    cols[1].markdown(item["summary"])

# -------------------- About & FAQ --------------------
with st.expander("ℹ️ About, Legend & FAQ"):
    st.markdown(f"""
**ALOG — Adaptive Local Quake Alert (Real-Time Safety) v10.8.1**  

- **Distance (km)** reference priority: **Manual Pin → IP → GPS/Coords**.  
- **Map**: marker clusters, optional density HeatMap overlay.  
- **Table**: small flags (Flagpedia or FlagCDN), dense rows, auto-fit 10 rows per page.  
- **Charts**: compact, readable; plus time-of-day × magnitude heatmap.  
    """)

st.markdown("<div class='footer'>ALOG Safety App v10.8.1 • Real-Time Safety</div>", unsafe_allow_html=True)
