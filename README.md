# 🌍 Quake Watch - Earthquake Monitor

Quake Watch is a **Streamlit-based dashboard** that monitors earthquake events worldwide using the **USGS Earthquake API**.  
It features real-time visualization, interactive maps, time-zone accurate logs, and pagination for efficient browsing.  

---

## ✨ Features
- 📡 **Live data** from USGS feeds (hour, day, or 7-day windows).  
- 🌍 **Location modes**:
  - Auto-detect via IP  
  - Country selection  
  - Manual latitude/longitude  
- 🕒 **Timezone-aware** logs (local timezone auto-detected from location).  
- 📊 **Events table**:
  - Latest logs with **pagination (10 per page)**  
  - Magnitude formatted to **1 decimal**  
  - Magnitude text **color-coded** (green/orange/red)  
- 🗺️ **Interactive map** (Folium):  
  - Circle markers sized by magnitude  
  - Tooltips showing event details and local time  
- 📈 **Magnitude trend chart** per page of logs  

---

## 🛠️ Requirements

Create a virtual environment and install the dependencies:

```bash
pip install -r requirements.txt
```

### `requirements.txt`
```
streamlit
streamlit-folium
folium
requests
matplotlib
pandas
pycountry
geopy
timezonefinder
pytz
```

---

## 🚀 Running Locally

1. Clone the repository or copy the project files.  
2. Ensure `quake_watch_web.py` and `requirements.txt` are in the same directory.  
3. Run the app:

```bash
streamlit run main.py
```

4. Open your browser at [http://localhost:8501](http://localhost:8501).  

---

## 🌐 Deployment

### Streamlit Cloud
1. Push the project to GitHub.  
2. Go to [Streamlit Cloud](https://streamlit.io/cloud).  
3. Deploy by selecting your repo and `quake_watch_web.py`.  
4. Make sure `requirements.txt` is in the repo root.  

---

## 📸 Screenshots

- **Table View** (paginated, color-coded magnitudes)  
- **Map View** (interactive with markers)  
- **Trend Chart** (page events only)  

---

## 📡 Data Source

All earthquake event data comes from the [USGS Earthquake Hazards Program](https://earthquake.usgs.gov/earthquakes/feed/).

---

## ⚖️ License

This project is provided for **educational and monitoring purposes**.  
Data accuracy and availability depend on the USGS API.

---

### 👨‍💻 Author
Built with ❤️ using **Streamlit** and **Folium**.  
