import os
import math
import requests
import pandas as pd
from flask import Flask, request, render_template_string
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

BASE_URL = "https://api.weather-ai.co"
API_KEY = os.getenv("WEATHERAI_API_KEY")


# -----------------------------
# Utility functions
# -----------------------------

def safe_get(d, keys, default=None):
    """Return the first available non-null value from a dictionary."""
    if not isinstance(d, dict):
        return default

    for key in keys:
        value = d.get(key)
        if value is not None:
            return value

    return default


def clipped_score(value, low, high):
    """Convert a raw value into a 0-100 risk score."""
    try:
        value = float(value)
    except Exception:
        return 0.0

    if high == low:
        return 0.0

    score = 100 * (value - low) / (high - low)
    return max(0.0, min(100.0, score))


def risk_level(score):
    """Convert numeric score into human-readable risk class."""
    if score >= 75:
        return "Extreme"
    if score >= 55:
        return "High"
    if score >= 30:
        return "Medium"
    return "Low"


def crop_thresholds(crop_type):
    """
    Crop-aware threshold configuration.
    These are model assumptions for decision-support scoring.
    """
    crop_type = str(crop_type).lower().strip()

    thresholds = {
        "heat_stress_c": 32,
        "cold_stress_c": 10,
        "heavy_rain_probability": 60,
        "very_heavy_rain_probability": 90,
        "wind_risk_kph": 35,
        "humidity_disease_pct": 85,
    }

    crop_specific = {
        "rice": {
            "heat_stress_c": 35,
            "cold_stress_c": 12,
            "humidity_disease_pct": 85,
        },
        "maize": {
            "heat_stress_c": 33,
            "cold_stress_c": 10,
            "humidity_disease_pct": 80,
        },
        "mango": {
            "heat_stress_c": 34,
            "cold_stress_c": 12,
            "humidity_disease_pct": 75,
        },
        "tea": {
            "heat_stress_c": 30,
            "cold_stress_c": 7,
            "humidity_disease_pct": 88,
        },
        "shrimp_aquaculture": {
            "heat_stress_c": 33,
            "cold_stress_c": 15,
            "humidity_disease_pct": 85,
        },
    }

    thresholds.update(crop_specific.get(crop_type, {}))
    return thresholds


# -----------------------------
# WeatherAI API
# -----------------------------

def fetch_weather(lat, lon, days=7):
    """Fetch weather data from WeatherAI free-plan compatible /v1/weather endpoint."""
    if not API_KEY:
        raise RuntimeError("Missing WEATHERAI_API_KEY environment variable.")

    url = f"{BASE_URL}/v1/weather"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {
        "lat": lat,
        "lon": lon,
        "days": min(int(days), 7),
        "units": "metric",
        "ai": "false",
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(f"WeatherAI API error {response.status_code}: {response.text}")

    return response.json()


def hourly_to_daily(hourly_list):
    """Aggregate WeatherAI hourly forecast data into daily weather features."""
    rows = []

    for h in hourly_list or []:
        time_value = safe_get(h, ["time", "datetime", "date"])
        if not time_value:
            continue

        rows.append({
            "date": str(time_value)[:10],
            "temp_hourly": safe_get(h, ["temperature", "temp"]),
            "rain_probability_hourly": safe_get(
                h,
                ["precipitation_probability", "rain_probability", "precip_probability", "pop"]
            ),
            "rain_mm_hourly": safe_get(
                h,
                ["precipitation", "rain", "rain_mm", "precip_mm", "precipitation_mm"]
            ),
            "wind_kph_hourly": safe_get(h, ["wind_speed", "wind_kph", "wind"]),
            "humidity_hourly": safe_get(h, ["humidity", "humidity_pct"]),
            "condition_hourly": safe_get(h, ["condition_code", "condition", "weather", "summary"]),
        })

    hdf = pd.DataFrame(rows)

    if hdf.empty:
        return pd.DataFrame()

    numeric_cols = [
        "temp_hourly",
        "rain_probability_hourly",
        "rain_mm_hourly",
        "wind_kph_hourly",
        "humidity_hourly",
    ]

    for col in numeric_cols:
        hdf[col] = pd.to_numeric(hdf[col], errors="coerce")

    return (
        hdf.groupby("date", as_index=False)
        .agg(
            temp_avg_c=("temp_hourly", "mean"),
            temp_min_from_hourly=("temp_hourly", "min"),
            temp_max_from_hourly=("temp_hourly", "max"),
            rain_probability=("rain_probability_hourly", "max"),
            rain_mm=("rain_mm_hourly", "sum"),
            wind_kph=("wind_kph_hourly", "mean"),
            max_wind_kph=("wind_kph_hourly", "max"),
            humidity_pct=("humidity_hourly", "mean"),
            max_humidity_pct=("humidity_hourly", "max"),
            condition=(
                "condition_hourly",
                lambda x: x.dropna().mode().iloc[0] if len(x.dropna().mode()) > 0 else "unknown"
            ),
        )
    )


def normalize_weather(data):
    """Combine daily forecast values with hourly fallback values."""
    daily_list = data.get("daily", []) or data.get("forecast", []) or data.get("days", [])
    hourly_list = data.get("hourly", []) or data.get("hours", [])

    hourly_daily = hourly_to_daily(hourly_list)

    daily_rows = []

    for i, day in enumerate(daily_list):
        daily_rows.append({
            "day_index": i + 1,
            "date": str(safe_get(day, ["date", "day", "datetime", "time"], ""))[:10],
            "temp_avg_c": safe_get(day, ["temp_avg", "avg_temp", "temperature_avg", "temperature", "temp"]),
            "temp_min_c": safe_get(day, ["temp_min", "min_temp", "temperature_min"]),
            "temp_max_c": safe_get(day, ["temp_max", "max_temp", "temperature_max"]),
            "rain_mm": safe_get(day, ["rain_mm", "rain", "precipitation", "precip_mm"]),
            "rain_probability": safe_get(day, ["rain_probability", "precipitation_probability", "pop"]),
            "wind_kph": safe_get(day, ["wind_kph", "wind_speed", "wind"]),
            "humidity_pct": safe_get(day, ["humidity", "humidity_pct"]),
            "condition": safe_get(day, ["condition", "condition_code", "summary", "weather"]),
        })

    df = pd.DataFrame(daily_rows)

    if df.empty and not hourly_daily.empty:
        df = hourly_daily.copy()
        df["day_index"] = range(1, len(df) + 1)
        df["temp_min_c"] = df["temp_min_from_hourly"]
        df["temp_max_c"] = df["temp_max_from_hourly"]

    if not df.empty and not hourly_daily.empty and "date" in df.columns:
        df = df.merge(hourly_daily, on="date", how="left", suffixes=("", "_hourlyfallback"))

        for col in ["temp_avg_c", "rain_probability", "rain_mm", "wind_kph", "humidity_pct", "condition"]:
            fallback_col = f"{col}_hourlyfallback"
            if fallback_col in df.columns:
                df[col] = df[col].where(df[col].notna(), df[fallback_col])

        if "temp_min_from_hourly" in df.columns:
            df["temp_min_c"] = df["temp_min_c"].where(df["temp_min_c"].notna(), df["temp_min_from_hourly"])
        if "temp_max_from_hourly" in df.columns:
            df["temp_max_c"] = df["temp_max_c"].where(df["temp_max_c"].notna(), df["temp_max_from_hourly"])

    numeric_cols = [
        "temp_avg_c", "temp_min_c", "temp_max_c",
        "rain_mm", "rain_probability", "wind_kph", "humidity_pct"
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "temp_avg_c" in df.columns:
        df["temp_avg_c"] = df["temp_avg_c"].fillna(df[["temp_min_c", "temp_max_c"]].mean(axis=1))

    df["rain_mm"] = df.get("rain_mm", pd.Series([0] * len(df))).fillna(0)
    df["rain_probability"] = df.get("rain_probability", pd.Series([0] * len(df))).fillna(0)
    df["wind_kph"] = df.get("wind_kph", pd.Series([0] * len(df))).fillna(0)
    df["humidity_pct"] = df.get("humidity_pct", pd.Series([0] * len(df))).fillna(0)
    df["condition"] = df.get("condition", pd.Series(["unknown"] * len(df))).fillna("unknown")

    return df.head(7)


# -----------------------------
# Risk model
# -----------------------------

def calculate_risk(df, crop_type):
    """Apply crop-aware farm weather risk scoring model."""
    thresholds = crop_thresholds(crop_type)
    rows = []

    for _, row in df.iterrows():
        heat_score = clipped_score(
            row.get("temp_max_c", 0),
            thresholds["heat_stress_c"],
            thresholds["heat_stress_c"] + 8,
        )

        cold_score = clipped_score(
            thresholds["cold_stress_c"] - row.get("temp_min_c", thresholds["cold_stress_c"]),
            0,
            8,
        )

        # Prefer actual rainfall if available; otherwise use precipitation probability.
        rain_mm = row.get("rain_mm", 0) or 0
        rain_probability = row.get("rain_probability", 0) or 0

        if rain_mm > 0:
            rain_score = clipped_score(rain_mm, 15, 50)
        else:
            rain_score = clipped_score(
                rain_probability,
                thresholds["heavy_rain_probability"],
                thresholds["very_heavy_rain_probability"],
            )

        wind_score = clipped_score(
            row.get("wind_kph", 0),
            thresholds["wind_risk_kph"],
            thresholds["wind_risk_kph"] + 35,
        )

        humidity_score = clipped_score(
            row.get("humidity_pct", 0),
            thresholds["humidity_disease_pct"],
            100,
        )

        composite_score = (
            0.30 * rain_score +
            0.25 * heat_score +
            0.15 * cold_score +
            0.15 * wind_score +
            0.15 * humidity_score
        )

        recommendations = generate_recommendations(
            rain_score, heat_score, cold_score, wind_score, humidity_score
        )

        rows.append({
            "date": row.get("date", ""),
            "temp_min_c": round(float(row.get("temp_min_c", 0) or 0), 1),
            "temp_max_c": round(float(row.get("temp_max_c", 0) or 0), 1),
            "temp_avg_c": round(float(row.get("temp_avg_c", 0) or 0), 1),
            "rain_probability": round(float(rain_probability), 1),
            "rain_mm": round(float(rain_mm), 1),
            "wind_kph": round(float(row.get("wind_kph", 0) or 0), 1),
            "humidity_pct": round(float(row.get("humidity_pct", 0) or 0), 1),
            "condition": str(row.get("condition", "unknown")),
            "heat_score": round(heat_score, 1),
            "cold_score": round(cold_score, 1),
            "rain_score": round(rain_score, 1),
            "wind_score": round(wind_score, 1),
            "humidity_disease_score": round(humidity_score, 1),
            "risk_score": round(composite_score, 1),
            "risk_level": risk_level(composite_score),
            "recommendations": recommendations,
        })

    risk_df = pd.DataFrame(rows)

    overall_score = round(float(risk_df["risk_score"].mean()), 1) if not risk_df.empty else 0
    worst_day = risk_df.sort_values("risk_score", ascending=False).iloc[0].to_dict() if not risk_df.empty else {}

    return risk_df, overall_score, risk_level(overall_score), worst_day


def generate_recommendations(rain_score, heat_score, cold_score, wind_score, humidity_score):
    recs = []

    if rain_score >= 55:
        recs.append("High rainfall probability: avoid fertilizer/pesticide application before rain.")
        recs.append("Check drainage and prepare for possible waterlogging.")
    elif rain_score >= 30:
        recs.append("Moderate rain risk: schedule field work during drier windows.")

    if heat_score >= 55:
        recs.append("Heat stress risk: irrigate early morning or late evening if possible.")
    elif heat_score >= 30:
        recs.append("Moderate heat: monitor soil moisture and young plants.")

    if cold_score >= 55:
        recs.append("Cold stress risk: protect seedlings and sensitive crops.")

    if wind_score >= 55:
        recs.append("Strong wind risk: avoid spraying and secure light structures.")
    elif wind_score >= 30:
        recs.append("Moderate wind: use caution with spraying operations.")

    if humidity_score >= 55:
        recs.append("High humidity disease pressure: scout for fungal symptoms.")
    elif humidity_score >= 30:
        recs.append("Moderate humidity disease pressure: monitor crop canopy conditions.")

    if not recs:
        recs.append("Low weather risk: suitable window for normal farm operations.")

    return recs


# -----------------------------
# Flask UI
# -----------------------------

HTML = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Farm Weather Risk Scorer</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f5f7fa;
            margin: 0;
            padding: 32px;
            color: #1f2937;
        }
        .container {
            max-width: 1100px;
            margin: auto;
        }
        .card {
            background: white;
            border-radius: 14px;
            padding: 24px;
            margin-bottom: 22px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.08);
        }
        h1, h2, h3 {
            margin-top: 0;
        }
        .subtitle {
            color: #6b7280;
            line-height: 1.5;
        }
        form {
            display: grid;
            grid-template-columns: repeat(2, minmax(220px, 1fr));
            gap: 16px;
        }
        label {
            font-weight: bold;
            display: block;
            margin-bottom: 6px;
        }
        input, select {
            width: 100%;
            padding: 10px;
            border: 1px solid #d1d5db;
            border-radius: 8px;
            font-size: 15px;
        }
        button {
            grid-column: 1 / -1;
            padding: 13px 18px;
            border: none;
            background: #0f766e;
            color: white;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
        }
        button:hover {
            background: #115e59;
        }
        .summary {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 14px;
        }
        .metric {
            background: #f0fdfa;
            border: 1px solid #99f6e4;
            padding: 16px;
            border-radius: 12px;
            text-align: center;
        }
        .metric strong {
            display: block;
            font-size: 28px;
            color: #0f766e;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }
        th, td {
            padding: 10px;
            border-bottom: 1px solid #e5e7eb;
            text-align: left;
        }
        th {
            background: #f9fafb;
        }
        .error {
            background: #fee2e2;
            color: #991b1b;
            padding: 16px;
            border-radius: 10px;
        }
        .pill {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 999px;
            background: #e0f2fe;
            color: #075985;
            font-weight: bold;
        }
        ul {
            margin-bottom: 0;
        }
        @media (max-width: 700px) {
            form, .summary {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
<div class="container">

    <div class="card">
        <h1>🌦️ Farm Weather Risk Scorer</h1>
        <p class="subtitle">
            A crop-aware geo-intelligence model that uses WeatherAI forecast data to estimate
            7-day agricultural weather risk and generate actionable farm recommendations.
        </p>
    </div>

    <div class="card">
        <h2>Analyze Location</h2>
        <form method="POST">
            <div>
                <label>Location Name</label>
                <input name="location_name" value="{{ form.location_name }}" placeholder="Dhaka, Bangladesh">
            </div>

            <div>
                <label>Crop Type</label>
                <select name="crop_type">
                    {% for crop in crops %}
                    <option value="{{ crop }}" {% if crop == form.crop_type %}selected{% endif %}>{{ crop }}</option>
                    {% endfor %}
                </select>
            </div>

            <div>
                <label>Latitude</label>
                <input name="lat" type="number" step="any" value="{{ form.lat }}" required>
            </div>

            <div>
                <label>Longitude</label>
                <input name="lon" type="number" step="any" value="{{ form.lon }}" required>
            </div>

            <div>
                <label>Land Acres</label>
                <input name="land_acres" type="number" step="any" value="{{ form.land_acres }}">
            </div>

            <button type="submit">Analyze Weather Risk</button>
        </form>
    </div>

    {% if error %}
    <div class="error">
        <strong>Error:</strong> {{ error }}
    </div>
    {% endif %}

    {% if result %}
    <div class="card">
        <h2>Overall Risk Summary</h2>
        <div class="summary">
            <div class="metric">
                <span>Average Risk Score</span>
                <strong>{{ result.overall_score }}/100</strong>
            </div>
            <div class="metric">
                <span>Risk Level</span>
                <strong>{{ result.overall_level }}</strong>
            </div>
            <div class="metric">
                <span>Highest-Risk Day</span>
                <strong>{{ result.worst_day.date }}</strong>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>Highest-Risk Day Details</h2>
        <p>
            <span class="pill">{{ result.worst_day.risk_level }}</span>
            Risk Score: <strong>{{ result.worst_day.risk_score }}/100</strong>
        </p>
        <ul>
            {% for rec in result.worst_day.recommendations %}
            <li>{{ rec }}</li>
            {% endfor %}
        </ul>
    </div>

    <div class="card">
        <h2>7-Day Forecast Risk Table</h2>
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Risk</th>
                    <th>Level</th>
                    <th>Temp Min</th>
                    <th>Temp Max</th>
                    <th>Rain Prob.</th>
                    <th>Wind</th>
                    <th>Humidity</th>
                    <th>Condition</th>
                </tr>
            </thead>
            <tbody>
                {% for row in result.rows %}
                <tr>
                    <td>{{ row.date }}</td>
                    <td>{{ row.risk_score }}</td>
                    <td>{{ row.risk_level }}</td>
                    <td>{{ row.temp_min_c }} °C</td>
                    <td>{{ row.temp_max_c }} °C</td>
                    <td>{{ row.rain_probability }}%</td>
                    <td>{{ row.wind_kph }} kph</td>
                    <td>{{ row.humidity_pct }}%</td>
                    <td>{{ row.condition }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% endif %}

</div>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    crops = ["rice", "maize", "mango", "tea", "shrimp_aquaculture", "generic"]

    form = {
        "location_name": "Dhaka, Bangladesh",
        "lat": "23.8103",
        "lon": "90.4125",
        "crop_type": "rice",
        "land_acres": "3.0",
    }

    result = None
    error = None

    if request.method == "POST":
        form = {
            "location_name": request.form.get("location_name", "Selected Location"),
            "lat": request.form.get("lat", "23.8103"),
            "lon": request.form.get("lon", "90.4125"),
            "crop_type": request.form.get("crop_type", "rice"),
            "land_acres": request.form.get("land_acres", "3.0"),
        }

        try:
            lat = float(form["lat"])
            lon = float(form["lon"])

            weather_json = fetch_weather(lat, lon, days=7)
            weather_df = normalize_weather(weather_json)
            risk_df, overall_score, overall_level, worst_day = calculate_risk(
                weather_df,
                form["crop_type"]
            )

            result = {
                "rows": risk_df.to_dict(orient="records"),
                "overall_score": overall_score,
                "overall_level": overall_level,
                "worst_day": worst_day,
            }

        except Exception as exc:
            error = str(exc)

    return render_template_string(
        HTML,
        crops=crops,
        form=form,
        result=result,
        error=error
    )


@app.route("/api/risk", methods=["GET"])
def api_risk():
    """
    JSON API endpoint example:
    /api/risk?lat=23.8103&lon=90.4125&crop_type=rice
    """
    try:
        lat = float(request.args.get("lat", 23.8103))
        lon = float(request.args.get("lon", 90.4125))
        crop_type = request.args.get("crop_type", "rice")

        weather_json = fetch_weather(lat, lon, days=7)
        weather_df = normalize_weather(weather_json)
        risk_df, overall_score, overall_level, worst_day = calculate_risk(weather_df, crop_type)

        return {
            "crop_type": crop_type,
            "lat": lat,
            "lon": lon,
            "overall_score": overall_score,
            "overall_level": overall_level,
            "worst_day": worst_day,
            "daily": risk_df.to_dict(orient="records"),
        }

    except Exception as exc:
        return {"error": str(exc)}, 500


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
