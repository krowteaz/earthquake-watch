import json, math, threading, time, ssl, os, requests, webbrowser, csv
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from geopy.geocoders import Nominatim
import folium
import pycountry
from tkinterweb import HtmlFrame   # Embedded map
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import winsound   # Windows built-in beep

APP_TITLE = "🌍 Quake Watch - Earthquake Monitor"
CONFIG_FILE = "quake_watch_config.json"

USGS_FEEDS = {
    "Past Hour (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "Past Day (all)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "Past 7 Days (M1.0+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_week.geojson",
    "Past 7 Days (M2.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
    "Past 7 Days (M4.5+)": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
}

# ------------------ Default Config ------------------
DEFAULT_CONFIG = {
    "latitude": 14.5995,          # Manila
    "longitude": 120.9842,
    "radius_km": 300,
    "min_mag": 3.0,
    "interval_sec": 60,
    "feed_name": "Past Hour (all)",
    "country": "Philippines"
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)   # auto create
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("Could not save config:", e)

# ------------------ Utilities ------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))

def fetch_geojson(url, timeout=12):
    ctx = ssl.create_default_context()
    req = Request(url, headers={"User-Agent": "QuakeWatch/1.0"})
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))

geolocator = Nominatim(user_agent="quake_watch_app")

def get_country(lat, lon):
    try:
        loc = geolocator.reverse((lat, lon), language="en", timeout=10)
        if loc and "country" in loc.raw.get("address", {}):
            return loc.raw["address"]["country"]
    except: pass
    return "Unknown"

def get_coords_from_country(country):
    try:
        loc = geolocator.geocode(country, timeout=10)
        if loc: return loc.latitude, loc.longitude
    except: pass
    return None, None

def get_my_location():
    try:
        data = requests.get("https://ipinfo.io/json", timeout=10).json()
        if "loc" in data:
            lat, lon = map(float, data["loc"].split(","))
            city = data.get("city",""); country = data.get("country","")
            label = ", ".join(x for x in [city,country] if x)
            return lat, lon, label
    except: pass
    return None, None, None

# ------------------ Main App ------------------
class QuakeWatchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1380x820")
        self.minsize(1200, 700)

        self.stop_event = threading.Event()
        self.seen_ids = set()
        self.chart_points = []

        self.alert_threshold = 5.5  # magnitude to trigger beep
        self.filter_by_country = tk.BooleanVar(value=False)

        self._build_ui()
        self._load_initial_config()
        self._render_map()
        self._render_chart()

    # --------- UI ---------
    def _build_ui(self):
        # Top
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        self.lat_var, self.lon_var = tk.StringVar(), tk.StringVar()
        self.radius_var, self.minmag_var, self.interval_var = tk.StringVar(value="300"), tk.StringVar(value="3.0"), tk.StringVar(value="60")
        self.feed_var, self.country_var = tk.StringVar(value="Past Hour (all)"), tk.StringVar()

        ttk.Label(top,text="Country:").pack(side=tk.LEFT)
        countries = sorted([c.name for c in pycountry.countries])
        cb = ttk.Combobox(top,textvariable=self.country_var,values=countries,state="readonly",width=25)
        cb.pack(side=tk.LEFT,padx=5)
        cb.bind("<<ComboboxSelected>>", self._on_country)

        ttk.Button(top,text="📍 Use My Location",command=self._use_my_location).pack(side=tk.LEFT,padx=5)

        # Country filter toggle
        ttk.Checkbutton(top,text="Filter by selected country only",variable=self.filter_by_country).pack(side=tk.LEFT,padx=15)

        ctl = ttk.Frame(self, padding=10)
        ctl.pack(side=tk.TOP, fill=tk.X)
        for label,var,w in [("Lat",self.lat_var,10),("Lon",self.lon_var,10),
                            ("Radius km",self.radius_var,8),("Min Mag",self.minmag_var,6),("Refresh sec",self.interval_var,8)]:
            ttk.Label(ctl,text=label+":").pack(side=tk.LEFT,padx=2)
            ttk.Entry(ctl,textvariable=var,width=w).pack(side=tk.LEFT,padx=4)

        ttk.Combobox(ctl,textvariable=self.feed_var,values=list(USGS_FEEDS.keys()),state="readonly",width=25).pack(side=tk.LEFT,padx=6)

        ttk.Button(ctl,text="▶ Start",command=self.start).pack(side=tk.LEFT,padx=3)
        ttk.Button(ctl,text="⏹ Stop",command=self.stop).pack(side=tk.LEFT,padx=3)
        ttk.Button(ctl,text="🗑 Clear",command=self.clear).pack(side=tk.LEFT,padx=3)
        ttk.Button(ctl,text="🌐 Open Map",command=self.open_map_browser).pack(side=tk.LEFT,padx=3)
        ttk.Button(ctl,text="💾 Export CSV",command=self.export_csv).pack(side=tk.LEFT,padx=3)

        # Paned
        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Table
        left = ttk.Frame(paned, padding=5)
        paned.add(left, weight=1)

        cols=("time","mag","dist","place","coords","id")
        self.tree = ttk.Treeview(left,columns=cols,show="headings")
        for col in cols: self.tree.heading(col,text=col.capitalize())
        self.tree.pack(fill=tk.BOTH,expand=True)

        # Right side
        right = ttk.Frame(paned, padding=5)
        paned.add(right, weight=1)
        right_paned = ttk.Panedwindow(right, orient=tk.VERTICAL)
        right_paned.pack(fill=tk.BOTH, expand=True)

        self.map_view = HtmlFrame(right_paned)
        right_paned.add(self.map_view, weight=3)

        chart_frame = ttk.Frame(right_paned)
        right_paned.add(chart_frame, weight=2)
        self.fig = Figure(figsize=(5,2.5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.status = tk.StringVar(value="Idle")
        ttk.Label(self,textvariable=self.status,anchor="w",relief=tk.SUNKEN).pack(side=tk.BOTTOM,fill=tk.X)

    # --------- Config ---------
    def _load_initial_config(self):
        cfg = load_config()
        self.lat_var.set(str(cfg.get("latitude",DEFAULT_CONFIG["latitude"])))
        self.lon_var.set(str(cfg.get("longitude",DEFAULT_CONFIG["longitude"])))
        self.radius_var.set(str(cfg.get("radius_km",DEFAULT_CONFIG["radius_km"])))
        self.minmag_var.set(str(cfg.get("min_mag",DEFAULT_CONFIG["min_mag"])))
        self.interval_var.set(str(cfg.get("interval_sec",DEFAULT_CONFIG["interval_sec"])))
        self.feed_var.set(cfg.get("feed_name",DEFAULT_CONFIG["feed_name"]))
        self.country_var.set(cfg.get("country",DEFAULT_CONFIG["country"]))

    def _save_config(self):
        try:
            cfg = {
                "latitude": float(self.lat_var.get()),
                "longitude": float(self.lon_var.get()),
                "radius_km": float(self.radius_var.get()),
                "min_mag": float(self.minmag_var.get()),
                "interval_sec": max(15,int(float(self.interval_var.get()))),
                "feed_name": self.feed_var.get(),
                "country": self.country_var.get(),
            }
            save_config(cfg)
        except: pass

    # --------- Actions ---------
    def _on_country(self,_):
        lat,lon=get_coords_from_country(self.country_var.get())
        if lat: self.lat_var.set(str(lat)); self.lon_var.set(str(lon)); self._render_map()

    def _use_my_location(self):
        lat,lon,label=get_my_location()
        if lat: self.lat_var.set(str(lat)); self.lon_var.set(str(lon))
        self.status.set(f"Detected: {label} ({lat:.2f},{lon:.2f})")
        self._render_map()

    def start(self):
        self.stop_event.clear(); self.seen_ids.clear(); self.chart_points.clear()
        self._save_config()
        threading.Thread(target=self._loop,daemon=True).start()
        self.status.set("Monitoring…")

    def stop(self): self.stop_event.set(); self.status.set("Stopped")
    def clear(self): [self.tree.delete(i) for i in self.tree.get_children()]; self.seen_ids.clear(); self.chart_points.clear(); self._render_chart(); self._render_map()

    def export_csv(self):
        file = filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV Files","*.csv")])
        if not file: return
        with open(file,"w",newline="",encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Time","Mag","Distance","Place","Coords","ID"])
            for iid in self.tree.get_children():
                writer.writerow(self.tree.item(iid)["values"])
        messagebox.showinfo("Export", f"Saved to {file}")

    def open_map_browser(self):
        path = os.path.join(os.getcwd(),"quake_map.html")
        if os.path.exists(path): webbrowser.open("file://"+path)

    # --------- Monitor Loop ---------
    def _loop(self):
        url=USGS_FEEDS[self.feed_var.get()]
        while not self.stop_event.is_set():
            try:
                lat,lon=float(self.lat_var.get()),float(self.lon_var.get())
                rad,mag,interval=float(self.radius_var.get()),float(self.minmag_var.get()),max(15,int(float(self.interval_var.get())))
            except: self.status.set("Invalid settings"); return
            try:
                feats=fetch_geojson(url).get("features",[])
                for f in feats:
                    qlon,qlat=f["geometry"]["coordinates"][:2]
                    qmag=f["properties"]["mag"] or 0; qid=f["id"]
                    place=f["properties"]["place"]; t=f["properties"]["time"]
                    dist=haversine_km(lat,lon,qlat,qlon)

                    cn = get_country(qlat,qlon)
                    if self.filter_by_country.get() and cn != self.country_var.get():
                        continue

                    if dist<=rad and qmag>=mag and qid not in self.seen_ids:
                        self.seen_ids.add(qid)
                        self._add_row(t,qmag,dist,f"{place} ({cn})",qlat,qlon,qid)
                        self.chart_points.append((datetime.fromtimestamp(t/1000), qmag))
                        if qmag >= self.alert_threshold:
                            winsound.Beep(1000, 800)  # Beep alert

                self._render_map(); self._render_chart()
                self.status.set(f"Last update {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception as e: self.status.set(str(e))
            time.sleep(interval)

    def _add_row(self,t,qmag,dist,place,qlat,qlon,qid):
        vals=(datetime.fromtimestamp(t/1000).strftime("%Y-%m-%d %H:%M:%S"),
              f"{qmag:.1f}",f"{dist:.1f}",place,f"{qlat:.2f},{qlon:.2f}",qid)
        self.after(0,lambda: self.tree.insert("",0,values=vals))

    # --------- Map & Chart ---------
    def _render_map(self):
        try: lat,lon=float(self.lat_var.get()),float(self.lon_var.get())
        except: return
        fmap=folium.Map(location=[lat,lon],zoom_start=4)
        folium.Marker([lat,lon],tooltip="You",icon=folium.Icon(color="blue")).add_to(fmap)
        for i in self.tree.get_children():
            v=self.tree.item(i)["values"]
            if v: qlat,qlon=map(float,v[4].split(","))
            folium.Marker([qlat,qlon],tooltip=f"M{v[1]} {v[3]}",icon=folium.Icon(color="red")).add_to(fmap)
        path=os.path.join(os.getcwd(),"quake_map.html"); fmap.save(path)
        self.map_view.load_file(path)

    def _render_chart(self):
        self.ax.clear()
        self.ax.set_title("Magnitude over time"); self.ax.set_xlabel("Time"); self.ax.set_ylabel("Mag")
        self.ax.grid(True,linestyle=":",linewidth=0.6)
        if self.chart_points:
            pts=sorted(self.chart_points,key=lambda p:p[0])[-100:]
            xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
            self.ax.plot(xs,ys,marker="o",linewidth=1)
            self.fig.autofmt_xdate()
        else:
            self.ax.text(0.5,0.5,"No events yet",transform=self.ax.transAxes,ha="center",va="center")
        self.canvas.draw_idle()

if __name__=="__main__":
    QuakeWatchApp().mainloop()
