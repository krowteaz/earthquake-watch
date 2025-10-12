
from typing import Optional, Tuple
try:
    from timezonefinder import TimezoneFinder  # type: ignore
except Exception:
    TimezoneFinder = None  # type: ignore
from math import radians, sin, cos, sqrt, atan2

def center_for_region(region: str) -> Tuple[float, float]:
    presets = {
        "Worldwide": (0.0, 0.0),
        "Philippines": (12.8797, 121.7740),
        "Japan": (36.2048, 138.2529),
        "Indonesia": (-0.7893, 113.9213),
        "Taiwan": (23.6978, 120.9605),
        "USA West": (36.5, -119.5),
        "Mexico": (23.6345, -102.5528),
        "Chile": (-35.6751, -71.5430),
        "New Zealand": (-41.0, 174.0),
        "Fiji": (-17.7134, 178.0650)
    }
    return presets.get(region, presets["Philippines"])

def get_timezone_for(lat: float, lon: float, tf: Optional["TimezoneFinder"] = None) -> Optional[str]:
    if TimezoneFinder is None:
        return "Asia/Manila"  # fallback
    if tf is None:
        tf = TimezoneFinder()
    try:
        tz = tf.timezone_at(lat=lat, lng=lon)
        return tz or "Asia/Manila"
    except Exception:
        return "Asia/Manila"

REGION_BBOX = {
    "Worldwide": None,
    "Philippines": (4.6, 21.3, 116.9, 126.9),
    "Japan": (24.0, 46.5, 123.0, 146.5),
    "Indonesia": (-11.0, 7.0, 95.0, 141.0),
    "Taiwan": (21.5, 25.5, 119.0, 123.5),
    "USA West": (30.0, 49.5, -125.0, -102.0),
    "Mexico": (14.0, 33.0, -118.0, -86.0),
    "Chile": (-56.0, -17.0, -76.0, -66.0),
    "New Zealand": (-47.5, -33.5, 166.0, 179.9),
    "Fiji_A": (-21.5, -12.0, 175.0, 180.0),
    "Fiji_B": (-21.5, -12.0, -180.0, -178.0),
}

def _in_bbox(lat: float, lon: float, bbox) -> bool:
    min_lat, max_lat, min_lon, max_lon = bbox
    return (min_lat <= lat <= max_lat) and (min_lon <= lon <= max_lon)

def in_region(lat: float, lon: float, region: str) -> bool:
    if region == "Worldwide" or region not in REGION_BBOX:
        return True
    if region == "Fiji":
        return _in_bbox(lat, lon, REGION_BBOX["Fiji_A"]) or _in_bbox(lat, lon, REGION_BBOX["Fiji_B"])
    return _in_bbox(lat, lon, REGION_BBOX[region])

def filter_df_by_region(df, region: str):
    if region == "Worldwide":
        return df.copy()
    mask = df.apply(lambda r: in_region(float(r["lat"]), float(r["lon"]), region), axis=1)
    return df[mask].reset_index(drop=True)

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def suggest_region_from_coords(lat: float, lon: float) -> Optional[str]:
    candidates = ["Philippines", "Japan", "Indonesia", "Taiwan", "USA West", "Mexico", "Chile", "New Zealand", "Fiji"]
    for r in candidates:
        if in_region(lat, lon, r):
            return r
    nearest = None
    best = 1e18
    for r in candidates:
        c_lat, c_lon = center_for_region(r)
        d = _haversine_km(lat, lon, c_lat, c_lon)
        if d < best:
            best = d; nearest = r
    return nearest
