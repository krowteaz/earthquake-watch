
import pandas as pd

def energy_release(mags):
    total = 0.0
    for m in mags:
        try:
            total += 10 ** (1.5 * float(m))
        except Exception:
            continue
    return total

def trend_indicator(df: "pd.DataFrame"):
    if df.empty or "mag" not in df: return "Insufficient data", "Not enough events."
    mags = df["mag"].astype(float)
    n = len(mags)
    if n < 3: return "Insufficient data", "Need more events to assess trend."
    recent = mags.iloc[:max(10, n//5)].mean()
    overall = mags.mean()
    majors = (mags >= 6.0).sum()
    label = "Stable"
    if recent > overall * 1.05 or majors >= 2: label = "Elevated"
    if recent > overall * 1.15 or majors >= 3: label = "High"
    reason = f"Recent avg {recent:.2f} vs overall {overall:.2f}; major count {majors}."
    return label, reason
