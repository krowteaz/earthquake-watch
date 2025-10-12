from typing import Tuple, Optional
from timezonefinder import TimezoneFinder

_TZF = TimezoneFinder()

_REGION_CENTERS = {
    "Worldwide": (0.0, 0.0),
    "Philippines": (12.8797, 121.7740),
    "Japan": (36.2048, 138.2529),
    "Indonesia": (-0.7893, 113.9213),
    "Taiwan": (23.6978, 120.9605),
    "USA West": (38.0, -122.0),
    "Mexico": (23.6345, -102.5528),
    "Chile": (-35.6751, -71.5430),
    "New Zealand": (-40.9006, 174.8860),
    "Fiji": (-17.7134, 178.0650),
}

def center_for_region(region: str) -> Tuple[float, float]:
    return _REGION_CENTERS.get(region, _REGION_CENTERS["Philippines"])

def suggest_region_from_coords(lat: float, lon: float) -> Optional[str]:
    if 4 <= lat <= 21 and 116 <= lon <= 127: return "Philippines"
    if 24 <= lat <= 46 and 123 <= lon <= 146: return "Japan"
    if -11 <= lat <= 6 and 95 <= lon <= 141: return "Indonesia"
    if 20 <= lat <= 26 and 118 <= lon <= 123: return "Taiwan"
    if 30 <= lat <= 49 and -125 <= lon <= -110: return "USA West"
    if 14 <= lat <= 33 and -118 <= lon <= -86: return "Mexico"
    if -56 <= lat <= -17 and -76 <= lon <= -66: return "Chile"
    if -48 <= lat <= -33 and 165 <= lon <= 180: return "New Zealand"
    if -22 <= lat <= -12 and 174 <= lon <= 180: return "Fiji"
    return "Worldwide"

def get_timezone_for(lat: float, lon: float) -> str:
    try:
        tz = _TZF.timezone_at(lat=lat, lng=lon)
        return tz or "Asia/Manila"
    except Exception:
        return "Asia/Manila"

def filter_df_by_region(df, region: str):
    if region == "Worldwide":
        return df
    latc, lonc = center_for_region(region)
    lat_min, lat_max = latc - 15, latc + 15
    lon_min, lon_max = lonc - 20, lonc + 20
    return df[(df["lat"]>=lat_min) & (df["lat"]<=lat_max) & (df["lon"]>=lon_min) & (df["lon"]<=lon_max)]
