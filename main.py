import streamlit as st
import pandas as pd
import pytz
import folium
from folium.plugins import MousePosition, MarkerCluster, HeatMap
from streamlit_folium import st_folium
from datetime import datetime, timezone, timedelta
from typing import Optional

from utils.geo import center_for_region, get_timezone_for, filter_df_by_region, suggest_region_from_coords
from utils.helpers import (
    USGS_FEEDS, fetch_usgs_feed, fetch_orfeus_feed, format_quake_row, safe_float, km_distance,
    magnitude_color, moving_average, unique_quake_id, ip_geolocate,
    fetch_quake_news, send_email_alert,
    country_code_from_place, flag_url_from_code, country_flag_from_code
)
from utils.forecast import energy_release, trend_indicator, project_hazard

st.set_page_config(page_title="ALOG • Real-Time Safety", page_icon="🛡️", layout="wide")

# Load CSS
try:
    with open("assets/style.css", "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except Exception:
    pass

st.sidebar.title("⚙️ Controls")

# ---- FlagCDN Control ----
st.sidebar.markdown("### 🏳️ FlagCDN Preview")
flag_size = st.sidebar.select_slider("Flag size (px)", options=[24, 32, 48, 64], value=48)
preview_code = st.session_state.get("region_value", "Philippines")
from utils.helpers import country_code_from_place, flag_url_from_code
flag_code = country_code_from_place(preview_code)
flag_url = flag_url_from_code(flag_code, size=flag_size)
st.sidebar.image(flag_url, width=flag_size)
st.sidebar.caption(f"{flag_code.upper()} via FlagCDN")


if "region_value" not in st.session_state: st.session_state["region_value"] = "Worldwide"
if "manual_pin" not in st.session_state: st.session_state["manual_pin"] = None
if "attempted_ip_auto" not in st.session_state: st.session_state["attempted_ip_auto"] = False
if "ip_coords" not in st.session_state: st.session_state["ip_coords"] = None
if "last_email_ids" not in st.session_state: st.session_state["last_email_ids"] = set()

st.sidebar.subheader("📍 Location")
gps_btn = st.sidebar.button("Use my location (GPS)")
ip_btn  = st.sidebar.button("Use my IP location")
auto_match = st.sidebar.toggle("🔄 Auto-match Region", value=True)

cluster_on = st.sidebar.toggle("🧩 Cluster markers", value=True)
heatmap_on = st.sidebar.toggle("🔥 HeatMap overlay", value=True)
heat_radius = st.sidebar.slider("Heat radius", 8, 30, 16, 1)

dense_rows = st.sidebar.toggle("Dense rows (table)", value=True)
table_height_mode = st.sidebar.selectbox("Table height", ["Auto-fit 10", "Tall (600px)", "Compact (480px)"], index=0)

source_choice = st.sidebar.selectbox("Earthquake Source", ["USGS", "ORFEUS (EU)"], index=0)
feed_name = st.sidebar.selectbox("USGS Feed", list(USGS_FEEDS.keys()), index=1) if source_choice=="USGS" else None

colA, colB = st.sidebar.columns(2)
min_mag   = colA.slider("Min Mag", 0.0, 9.9, 2.5, 0.1)
notify_mag= colB.slider("Notify ≥", 0.0, 9.9, 5.0, 0.1)

st.sidebar.subheader("⏱️ Auto Refresh")
refresh_map = {"Off": 0, "1 minute": 60, "5 minutes": 300, "15 minutes": 900}
refresh_choice = st.sidebar.selectbox("Refresh every…", list(refresh_map.keys()), index=1)
if refresh_map[refresh_choice] > 0:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh_map[refresh_choice]*1000, key="refresh")

# Query params
q = st.query_params
lat_val = q.get("lat"); lon_val = q.get("lon"); src_param = q.get("src")
lat = safe_float(lat_val[0] if isinstance(lat_val, list) else lat_val)
lon = safe_float(lon_val[0] if isinstance(lon_val, list) else lon_val)
src_param = (src_param[0] if isinstance(src_param, list) else src_param) or None
location_source: Optional[str] = None

if gps_btn:
    st.components.v1.html("""
    <script>
      (function(){
        if(!navigator.geolocation){ alert("Geolocation unsupported."); return; }
        navigator.geolocation.getCurrentPosition(function(pos){
          const url = new URL(window.location.href);
          url.searchParams.set('lat', pos.coords.latitude.toFixed(6));
          url.searchParams.set('lon', pos.coords.longitude.toFixed(6));
          url.searchParams.set('src', 'gps');
          window.location.href = url.toString();
        }, function(err){ alert("Location error: "+err.message); }, {enableHighAccuracy:true, timeout:10000, maximumAge:0});
      })();
    </script>
    """, height=0, width=0)

ip_meta = None
if (lat is None or lon is None) and ip_btn:
    ip_lat, ip_lon, _, meta = ip_geolocate()
    if ip_lat is not None and ip_lon is not None:
        lat, lon, location_source, ip_meta = ip_lat, ip_lon, "IP", meta
        st.session_state["ip_coords"] = (ip_lat, ip_lon)
        st.query_params.update({"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "src": "ip"})
    else:
        st.sidebar.error("Could not get IP location.")

if (lat is None or lon is None) and not st.session_state["attempted_ip_auto"]:
    ip_lat, ip_lon, _, meta = ip_geolocate()
    st.session_state["attempted_ip_auto"] = True
    if ip_lat is not None and ip_lon is not None:
        lat, lon, location_source, ip_meta = ip_lat, ip_lon, "IP", meta
        st.session_state["ip_coords"] = (ip_lat, ip_lon)

user_has_coords = (lat is not None and lon is not None)
if src_param == "gps": location_source = "GPS"
elif src_param == "pin": location_source = "Manual Pin"
elif src_param == "ip": location_source = "IP"
elif location_source is None and user_has_coords: location_source = "GPS/Manual"

if user_has_coords and auto_match:
    suggested = suggest_region_from_coords(lat, lon)
    if suggested and suggested != st.session_state["region_value"]:
        st.session_state["region_value"] = suggested

REGION_OPTIONS = ["Worldwide","Philippines","Japan","Indonesia","Taiwan","USA West","Mexico","Chile","New Zealand","Fiji"]
region_index = REGION_OPTIONS.index(st.session_state["region_value"]) if st.session_state["region_value"] in REGION_OPTIONS else 0
region_effective = st.sidebar.selectbox("Region", REGION_OPTIONS, index=region_index)
st.session_state["region_value"] = region_effective

if user_has_coords:
    tz_name = get_timezone_for(lat, lon)
else:
    cen = center_for_region(region_effective)
    tz_name = get_timezone_for(cen[0], cen[1])
local_tz = pytz.timezone(tz_name)

st.title("🛡️ ALOG — Real-Time Safety v10.9.4")

status_label = "No location set"; status_class = "pill none"
if user_has_coords:
    if location_source == "GPS": status_label, status_class = "GPS", "pill gps"
    elif location_source == "IP": status_label, status_class = "IP", "pill ip"
    elif "Manual" in (location_source or ""): status_label, status_class = "Manual Pin", "pill manual"
    else: status_label, status_class = "Coords set", "pill neutral"
st.markdown(f"<div class='statusbar'>Location: <span class='{status_class}'>{status_label}</span> • Region: <b>{region_effective}</b> • TZ: <code>{tz_name}</code></div>", unsafe_allow_html=True)

with st.spinner("Fetching earthquakes…"):
    data = fetch_usgs_feed(USGS_FEEDS[feed_name]) if source_choice=="USGS" else fetch_orfeus_feed()

rows=[]
for feat in data.get("features", []):
    row = format_quake_row(feat)
    if row: rows.append(row)
df_raw = pd.DataFrame(rows)
if df_raw.empty:
    st.warning("No data from selected source.")
    st.stop()

def to_local(dt_naive_utc):
    try:
        aware_utc = pytz.utc.localize(dt_naive_utc)
        local_dt  = aware_utc.astimezone(local_tz)
        return local_dt, local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return None, ""
if "time_utc" in df_raw.columns:
    conv = df_raw["time_utc"].apply(to_local)
    df_raw["time_local"] = [c[0] for c in conv]
    df_raw["time_local_str"] = [c[1] for c in conv]

df = filter_df_by_region(df_raw, region_effective)

ref_source="None"; ref_lat=ref_lon=None
if st.session_state.get("manual_pin"):
    ref_lat, ref_lon = st.session_state["manual_pin"]; ref_source="Manual Pin"
elif st.session_state.get("ip_coords"):
    ref_lat, ref_lon = st.session_state["ip_coords"]; ref_source="IP"
elif user_has_coords:
    ref_lat, ref_lon = lat, lon; ref_source="GPS/Coords"

if ref_lat is not None and ref_lon is not None:
    df["distance_km"] = df.apply(lambda r: km_distance(ref_lat, ref_lon, r["lat"], r["lon"]), axis=1)
else:
    df["distance_km"] = None

df["flag_code"]  = df["place"].apply(country_code_from_place).astype(str)
df["flag_img"]   = df["flag_code"].apply(lambda c: flag_url_from_code(c))
df["flag_emoji"] = df["flag_code"].apply(lambda c: country_flag_from_code(c))

def distance_bucket(d):
    if d is None: return ("Unknown", "#6b7280", "❔")
    if d <= 300:  return ("Local Critical", "#ef4444", "🔴")
    if d <= 600:  return ("Regional Watch", "#f59e0b", "🟡")
    if d <= 1000: return ("Extended Zone", "#60a5fa", "🔵")
    return ("Distant Info", "#22c55e", "🟢")

df[["dist_label","dist_color","dist_icon"]] = df.apply(lambda r: pd.Series(distance_bucket(r.get("distance_km"))), axis=1)

df = df[df["mag"].fillna(0) >= min_mag].sort_values("time_utc", ascending=False).reset_index(drop=True)
if df.empty:
    st.info(f"No events in {region_effective} for current filters.")
    st.stop()

def browser_notify_js(title, body, play_sound=False):
    audio = """var a=new Audio('assets/alert.wav');a.play().catch(()=>{});""" if play_sound else ""
    return f"""
    <script>
      (function(){{
        try {{
          if (!("Notification" in window)) return;
          function ping(){{ new Notification("{title}", {{ body: "{body}" }}); }}
          if (Notification.permission === "granted") {{ ping(); {audio} }}
          else if (Notification.permission !== "denied") {{
            Notification.requestPermission().then(function(p){{ if(p==="granted"){{ ping(); {audio} }} }});
          }}
        }} catch(e) {{}}
      }})();
    </script>
    """

top = df.iloc[0] if not df.empty else None
if top is not None and top["mag"] >= 5.0:
    st.markdown(
        f"<div style='padding:10px;border-radius:10px;background:#7f1d1d;color:#fff;font-weight:700'>"
        f"🚨 EMERGENCY: M{top['mag']:.1f} • {top['place']} • {top.get('time_local_str','')}" 
        f"</div>", unsafe_allow_html=True
    )
    st.components.v1.html(browser_notify_js(f"Quake M{top['mag']:.1f}", top['place'][:48], play_sound=True), height=0, width=0)
    eid = unique_quake_id(top)
    if eid not in st.session_state["last_email_ids"]:
        subject = f"[ALOG EMERGENCY] M{top['mag']:.1f} • {top['place']}"
        body = (
            f"Magnitude: {top['mag']}\n"
            f"Location: {top['place']}\n"
            f"Local Time: {top.get('time_local_str','')}\n"
            f"Coordinates: {top['lat']:.3f},{top['lon']:.3f}\n"
            f"Depth: {top.get('depth_km','?')} km\n"
            f"Source: {source_choice}\n"
        )
        ok = send_email_alert(subject, body)
        st.session_state["last_email_ids"].add(eid)
        st.caption("📧 Email sent" if ok else "⚠️ Email failed (check .env)")

tab_live, tab_forecast, tab_proj, tab_news = st.tabs(["🌐 Live Alerts", "📊 Forecast & Trends", "🧭 Projections", "📰 News"])

with tab_live:
    center = center_for_region(region_effective) if region_effective!="Worldwide" else (0,0)
    m = folium.Map(location=center, zoom_start=5 if region_effective!="Worldwide" else 2, control_scale=True)
    st.caption("🖱️ Click map to set a Manual Pin.")

    if st.session_state.get("manual_pin"):
        pin_lat, pin_lon = st.session_state["manual_pin"]
        folium.Marker(location=(pin_lat, pin_lon), tooltip="Manual Pin", icon=folium.Icon(color="blue")).add_to(m)
    elif st.session_state.get("ip_coords"):
        ip_lat, ip_lon = st.session_state["ip_coords"]
        folium.Marker(location=(ip_lat, ip_lon), tooltip="IP location", icon=folium.Icon(color="green")).add_to(m)
    elif user_has_coords:
        folium.Marker(location=(lat, lon), tooltip="Your location", icon=folium.Icon(icon="user")).add_to(m)

    cluster_layer = MarkerCluster(name="Quakes") if cluster_on else None
    bounds=[]; heat_points=[]
    for _, r in df.iterrows():
        lat_q, lon_q = float(r["lat"]), float(r["lon"])
        mag = float(r["mag"]) if pd.notna(r["mag"]) else 0.0
        depth = r.get("depth_km")
        mag_fill = magnitude_color(mag)
        stroke = r.get("dist_color") or "#000"
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
            location=(lat_q, lon_q), radius=radius, color=stroke, weight=3,
            fill=True, fill_color=mag_fill, fill_opacity=0.8,
            tooltip=f"M{mag:.1f} • {r.get('place','')}",
            popup=folium.Popup(popup_html, max_width=320)
        )
        if cluster_layer: cluster_layer.add_child(marker)
        else: marker.add_to(m)
        bounds.append((lat_q, lon_q)); heat_points.append([lat_q, lon_q, max(0.5, mag - 1.5)])

    if cluster_layer: cluster_layer.add_to(m)

    if heatmap_on and heat_points:
        # Normalize intensity for visibility even with few points
        max_val = max([p[2] for p in heat_points]) if heat_points else 1.0
        norm_points = [[p[0], p[1], max(0.2, min(1.0, p[2] / max_val))] for p in heat_points]
        HeatMap(
            norm_points,
            radius=heat_radius,
            blur=int(heat_radius * 0.85),
            min_opacity=0.25,
            max_opacity=0.9,
            gradient={0.2: 'blue', 0.4: 'lime', 0.65: 'yellow', 0.85: 'orange', 1.0: 'red'}
        ).add_to(m)

    MousePosition(position="bottomright", separator=" | ", prefix="Lat/Lon").add_to(m)
    if bounds:
        try: m.fit_bounds(bounds, padding=(20,20))
        except Exception: pass

    st_data = st_folium(m, height=560, use_container_width=True, key="live_map", returned_objects=['last_clicked'])
    if st_data and st_data.get("last_clicked"):
        pin_lat = float(st_data["last_clicked"]["lat"]); pin_lon = float(st_data["last_clicked"]["lng"])
        st.session_state["manual_pin"] = (pin_lat, pin_lon)
        st.query_params.update({"lat": f"{pin_lat:.6f}", "lon": f"{pin_lon:.6f}", "src":"pin"})
        st.success(f"Manual pin set: {pin_lat:.4f}, {pin_lon:.4f}")
        if auto_match:
            suggested = suggest_region_from_coords(pin_lat, pin_lon)
            if suggested and suggested != st.session_state["region_value"]:
                st.session_state["region_value"] = suggested
        st.rerun()

    # Stats + table
    st.subheader("Events & Tally")
    ref_txt = f"{ref_source} @ {ref_lat:.2f},{ref_lon:.2f}" if ref_lat is not None else "None"
    st.caption(f"Region: **{region_effective}** • Source: {source_choice} • TZ: {tz_name} • Distance ref: {ref_txt} • Showing {len(df)} (M ≥ {min_mag})")

    now_local = datetime.now(local_tz)
    total=len(df); majors=int((df["mag"]>=6.0).sum())
    lcrit=int((df["dist_label"]=="Local Critical").sum())
    lwat=int((df["dist_label"]=="Regional Watch").sum())
    lext=int((df["dist_label"]=="Extended Zone").sum())
    lfar=int((df["dist_label"]=="Distant Info").sum())
    s1,c1,c2,c3,c4,s2=st.columns([0.1,1,1,1,1,0.1])
    c1.metric("🌍 Total", total); c2.metric("⚡ Major (≥6.0)", majors); c3.metric("Last Refresh", now_local.strftime("%Y-%m-%d %H:%M:%S")); c4.metric("🔴 ≤300", lcrit)

    st.markdown("#### Live Events (10 per page)")
    df_table = df.copy().reset_index(drop=True)
    df_table["Flag"] = df_table["flag_img"]
    df_table["Region"] = df_table["flag_emoji"] + " " + df_table["place"].astype(str)
    df_table = df_table[["Flag","Region","time_local_str","mag","depth_km","lat","lon","distance_km","dist_label"]].rename(columns={
        "time_local_str":"Local Time","mag":"Mag","depth_km":"Depth (km)","lat":"Lat","lon":"Lon","distance_km":"Dist (km)","dist_label":"Distance Class"
    })
    df_table["Mag"] = df_table["Mag"].map(lambda x: round(float(x),1) if pd.notna(x) else x)
    if df_table["Dist (km)"].notna().any():
        df_table["Dist (km)"] = df_table["Dist (km)"].map(lambda x: round(x) if pd.notna(x) else x)

    page_size=10
    total_pages=max(1,(len(df_table)+page_size-1)//page_size)
    page_num=st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1, key="live_table_page")
    start=(page_num-1)*page_size; end_idx=start+page_size
    shown=df_table.iloc[start:end_idx]
    st.caption(f"Showing {start+1}–{min(end_idx, len(df_table))} of {len(df_table)}")

    header_px=52 if dense_rows else 56
    row_px=36 if dense_rows else 44
    height = max(300, min(780, header_px + row_px * len(shown))) if table_height_mode=="Auto-fit 10" else (600 if table_height_mode=="Tall (600px)" else 480)

    if dense_rows: st.markdown("<div class='dense-table'>", unsafe_allow_html=True)
    st.dataframe(shown, use_container_width=True, height=height, hide_index=True,
        column_config={
            "Flag": st.column_config.ImageColumn("Flag", help="Flag images from FlagCDN.", width="small"),
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
    if dense_rows: st.markdown("</div>", unsafe_allow_html=True)

with tab_forecast:
    st.subheader("Forecast & Trends (Heuristic)")
    if len(df) >= 3:
        df_asc = df.sort_values("time_utc").reset_index(drop=True)
        mags = df_asc["mag"].astype(float).tolist()
        ma = moving_average(mags, window=min(7, max(3, len(mags)//4)))
        total_energy = energy_release(mags)
        indicator, reason = trend_indicator(df_asc)
        c1,c2,c3 = st.columns(3)
        c1.metric("Events", len(df)); c2.metric("Energy Index (rel.)", f"{total_energy:,.0f}"); c3.metric("Activity", indicator)
        st.caption(reason)
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6.0, 3.0), dpi=120)
        ax.plot(range(len(mags)), mags, linewidth=1.8, marker='o', markersize=3, label="Magnitude")
        ax.plot(range(len(ma)), ma, linewidth=2.2, alpha=0.9, label="Moving Avg")
        ax.set_xlabel("Event order (old → recent)"); ax.set_ylabel("Magnitude"); ax.set_title("Magnitude & Moving Average")
        ax.grid(True, alpha=0.3); ax.legend(fontsize=9, loc="best")
        st.pyplot(fig, use_container_width=False)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        def count_in(days): return int((df["time_utc"] >= (now_utc - timedelta(days=days))).sum())
        colx,coly,colz = st.columns(3)
        colx.metric("Last 24h", count_in(1)); coly.metric("Last 7d", count_in(7)); colz.metric("Last 30d", count_in(30))
    else:
        st.info("Not enough data for meaningful trends.")

with tab_proj:
    st.subheader("Nowcast Projections (Ring of Fire–weighted)")
    st.caption("Heuristic near-term hazard from clusters + time-decayed KDE. Not a prediction—situational guidance only.")
    horizon = st.slider("Horizon (hours)", 12, 96, 48, 6)
    out = project_hazard(df, horizon_hours=horizon)

    center = center_for_region(region_effective) if region_effective!="Worldwide" else (0,0)
    m2 = folium.Map(location=center, zoom_start=5 if region_effective!="Worldwide" else 2, control_scale=True)
    pts = [[p["lat"], p["lon"], max(0.01, float(p["risk"]))] for p in out["grid_points"]]
    if pts:
        HeatMap(
            pts,
            radius=24,
            blur=20,
            min_opacity=0.3,
            max_opacity=0.9,
            gradient={0.1: 'blue', 0.3: 'lime', 0.6: 'yellow', 0.8: 'orange', 1.0: 'red'}
        ).add_to(m2)
        MousePosition(position="bottomright", separator=" | ", prefix="Lat/Lon").add_to(m2)
        st_folium(m2, height=520, use_container_width=True, key="proj_map")
    else:
        st.info("Not enough data to build a projection heat layer. Try a longer feed or lower Min Mag.")

    met = out["metrics"]
    c1, c2, c3 = st.columns(3)
    c1.metric("b-value", "—" if met["b_value"] is None else f"{met['b_value']:.2f}")
    c2.metric("Expected 24h", met["expected_events_24h"])
    c3.metric("Active Clusters", met["active_clusters"])

with tab_news:
    st.subheader("📰 Earthquake News")
    only_imgs = st.checkbox("Only stories with images", value=False)
    news_items = fetch_quake_news(st.session_state["region_value"], limit=12)
    if only_imgs:
        news_items = [n for n in news_items if n.get("image")]
    if not news_items:
        st.info("No news found right now.")
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
                meta = [x for x in [item.get("published","")] if x]
                if meta: cols[1].caption(" • ".join(meta))
                if item.get("summary"): cols[1].markdown(item["summary"])
