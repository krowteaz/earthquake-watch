from math import radians, sin, cos, sqrt, atan2, exp, log10
from datetime import datetime, timezone
from typing import List, Tuple, Optional

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1); dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

_RING_POINTS = [
    (60,-150),(55,-140),(50,-135),(45,-130),(40,-125),(35,-122),(30,-116),(20,-106),
    (10,-90),(0,-80),(-10,-75),(-20,-72),(-30,-72),(-40,-73),(-50,-74),
    (-40,175),(-35,178),(-30,180),(-15,170),(0,165),(10,160),(15,150),
    (5,135),(-5,125),(-5,115),(0,110),(5,105),(10,100),(15,95),(20,90),
    (20,130),(25,135),(30,140),(35,145),(40,145),(45,150),(50,160)
]

def _ring_weight(lat, lon):
    dmin = min(_haversine_km(lat, lon, rlat, rlon) for (rlat, rlon) in _RING_POINTS)
    if dmin <= 500:  return 1.25
    if dmin <= 1000: return 1.10
    return 1.0

def _b_value(mags: List[float], mmin: Optional[float] = None) -> Optional[float]:
    clean = [float(m) for m in mags if m is not None]
    if len(clean) < 8: return None
    if mmin is None: mmin = max(2.5, min(clean))
    mean_m = sum(clean)/len(clean)
    if mean_m <= mmin: return 1.0
    return (log10(2.718281828)) / (mean_m - (mmin - 0.05))

def _cluster(points: List[Tuple[float,float]], eps_km=120.0, min_pts=3):
    clusters=[]; visited=[False]*len(points); assigned=[-1]*len(points)
    def nbrs(i):
        li,lo = points[i]
        return [j for j,(la,lo2) in enumerate(points) if _haversine_km(li,lo,la,lo2) <= eps_km]
    cid=0
    for i in range(len(points)):
        if visited[i]: continue
        visited[i]=True
        n=nbrs(i)
        if len(n)<min_pts: continue
        clusters.append([]); assigned[i]=cid; clusters[cid].append(i)
        seed=[x for x in n if x!=i]
        while seed:
            j=seed.pop()
            if not visited[j]:
                visited[j]=True
                nj=nbrs(j)
                if len(nj)>=min_pts:
                    for x in nj:
                        if x not in seed: seed.append(x)
            if assigned[j]==-1:
                assigned[j]=cid; clusters[cid].append(j)
        cid+=1
    return clusters, assigned

def _grid_bounds(df):
    lats=[float(r["lat"]) for _,r in df.iterrows()]
    lons=[float(r["lon"]) for _,r in df.iterrows()]
    return min(lats), max(lats), min(lons), max(lons)

def _make_grid(bounds, step_km=100.0):
    from math import radians, cos
    lat_min, lat_max, lon_min, lon_max = bounds
    lat_mid = (lat_min + lat_max)/2.0 if lat_max>=lat_min else 0.0
    dlat = step_km/111.0
    dlon = step_km/(111.0*max(0.2, cos(radians(lat_mid))))
    grid=[]
    lat=lat_min
    while lat <= lat_max:
        lon=lon_min
        while lon <= lon_max:
            grid.append((lat,lon))
            lon += dlon
        lat += dlat
    return grid

def _decay_kernel(dist_km: float, dt_hours: float, space_sigma_km=180.0, time_half_life_h=36.0) -> float:
    s = exp(-(dist_km**2)/(2*space_sigma_km**2))
    lam = exp(-dt_hours * 0.69314718 / time_half_life_h)
    return s * lam

def project_hazard(df, horizon_hours: int = 48):
    if df is None or df.empty:
        return {"grid_points": [], "metrics": {"b_value": None, "expected_events_24h": 0, "expected_events_72h": 0, "active_clusters": 0}}
    rows=[]
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    for _,r in df.iterrows():
        t = r.get("time_utc")
        if t is None: continue
        try:
            rows.append((float(r["lat"]), float(r["lon"]), float(r["mag"]) if r.get("mag") is not None else None, max(0.0,(now_utc-t).total_seconds()/3600.0)))
        except Exception:
            pass
    if not rows:
        return {"grid_points": [], "metrics": {"b_value": None, "expected_events_24h": 0, "expected_events_72h": 0, "active_clusters": 0}}

    pts=[(la,lo) for (la,lo,_,_) in rows]
    clusters,_ = _cluster(pts, eps_km=120.0, min_pts=3)
    active_clusters=len(clusters)

    bounds=_grid_bounds(df)
    grid=_make_grid(bounds, step_km=100.0)
    scored=[]
    for glat,glon in grid:
        s=0.0
        for (la,lo,mg,dt) in rows:
            dist=_haversine_km(glat,glon,la,lo)
            base=_decay_kernel(dist, dt)
            mwt=(10 ** (1.5 * ((mg if mg is not None else 2.5)))) ** 0.15
            s += base*mwt
        scored.append((glat,glon,s*_ring_weight(glat,glon)))
    mx=max([s for _,_,s in scored] or [1.0])
    grid_points=[{"lat":glat,"lon":glon,"risk": (s/mx if mx>0 else 0.0)} for (glat,glon,s) in scored]

    mags=[m for (_,_,m,_) in rows if m is not None]
    bval=_b_value(mags)
    last24=sum(1 for *_,dt in rows if dt<=24.0)
    exp24=round(max(1,last24)*1.1)
    exp72=round(max(1,last24)*2.8)

    return {"grid_points": grid_points, "metrics": {
        "b_value": round(bval,2) if bval is not None else None,
        "expected_events_24h": exp24,
        "expected_events_72h": exp72,
        "active_clusters": active_clusters
    }}

def energy_release(mags: List[float]):
    total=0.0
    for m in mags:
        try: total += 10**(1.5*float(m))
        except: pass
    return total

def trend_indicator(df):
    if df.empty or "mag" not in df: return "Insufficient data", "Not enough events."
    mags=df["mag"].astype(float); n=len(mags)
    if n<3: return "Insufficient data", "Need more events."
    recent=mags.tail(max(10, n//5)).mean(); overall=mags.mean(); majors=(mags>=6.0).sum()
    label="Stable"
    if recent>overall*1.05 or majors>=2: label="Elevated"
    if recent>overall*1.15 or majors>=3: label="High"
    return label, f"Recent {recent:.2f} vs overall {overall:.2f}; majors {majors}."
