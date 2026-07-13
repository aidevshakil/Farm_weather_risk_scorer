# Farm Weather Risk Scorer

Geo-intelligence weather risk modeling using the WeatherAI API.

## Overview

Farm Weather Risk Scorer is a Flask application that converts WeatherAI forecast data into a 7-day agricultural weather risk assessment. Instead of only showing raw weather values, the app normalizes forecast data, applies crop-aware thresholds, scores weather hazards, and returns practical farm recommendations.

The project supports both a browser interface and a JSON API endpoint.

Core capabilities:

- Fetch 7-day forecast data from WeatherAI.
- Normalize daily and hourly forecast structures into a consistent daily dataset.
- Score heat, cold, rainfall, wind, and humidity disease pressure.
- Adjust risk thresholds by crop type.
- Produce an overall risk score, risk level, highest-risk day, and daily recommendations.
- Expose results through a Flask web page and `/api/risk` endpoint.

## Project Structure

```text
weather_risk/
|-- app.py                         # Flask app, WeatherAI client, normalization, risk model, routes
|-- Farm_weather_risk_scorer.ipynb  # Notebook for exploratory analysis and model demonstration
|-- requirements.txt               # Python dependencies
|-- README.md                      # Project documentation
|-- .env                           # Local environment variables, not committed
|-- .gitignore                     # Ignored local and generated files
```

## Methodology

### 1. User Input

The application starts with a location and crop profile:

- Location name
- Latitude
- Longitude
- Crop type
- Land area in acres

The current crop options are:

- `rice`
- `maize`
- `mango`
- `tea`
- `shrimp_aquaculture`
- `generic`

Latitude, longitude, and crop type drive the forecast request and crop-aware risk thresholds.

### 2. Weather Data Collection

Weather data is requested from WeatherAI's free-plan compatible endpoint:

```http
GET /v1/weather
```

The request is made with:

- `lat`
- `lon`
- `days`, capped at 7
- `units=metric`
- `ai=false`
- Bearer authentication using `WEATHERAI_API_KEY`

The API response may contain daily forecast values, hourly forecast values, or both. The app is designed to handle incomplete or differently named fields.

### 3. Forecast Normalization

WeatherAI responses can vary by structure and field naming. The normalization layer standardizes those responses before scoring.

The app uses `safe_get()` to read the first available non-null value from several possible field names. For example, temperature may appear as `temperature`, `temp`, `temp_avg`, or another related key.

Daily data is normalized into these core fields:

- `date`
- `temp_avg_c`
- `temp_min_c`
- `temp_max_c`
- `rain_mm`
- `rain_probability`
- `wind_kph`
- `humidity_pct`
- `condition`

If daily values are missing, the app aggregates hourly values into daily features:

- Average, minimum, and maximum temperature
- Maximum rain probability
- Total rainfall
- Average and maximum wind speed
- Average and maximum humidity
- Most common condition

This fallback keeps the model usable even when the daily forecast section is incomplete.

### 4. Crop-Aware Threshold Selection

The model begins with generic agricultural thresholds and then applies crop-specific overrides.

Generic thresholds:

| Threshold | Default Value |
| --- | ---: |
| Heat stress | 32 C |
| Cold stress | 10 C |
| Heavy rain probability | 60% |
| Very heavy rain probability | 90% |
| Wind risk | 35 kph |
| Humidity disease pressure | 85% |

Crop overrides:

| Crop | Heat Stress | Cold Stress | Humidity Disease |
| --- | ---: | ---: | ---: |
| Rice | 35 C | 12 C | 85% |
| Maize | 33 C | 10 C | 80% |
| Mango | 34 C | 12 C | 75% |
| Tea | 30 C | 7 C | 88% |
| Shrimp aquaculture | 33 C | 15 C | 85% |

These thresholds are model assumptions used for decision-support scoring, not agronomic guarantees.

### 5. Component Risk Scoring

Each environmental hazard is converted into a `0-100` score using `clipped_score()`.

The helper maps a raw value between a low and high boundary:

```text
score = 100 * (value - low) / (high - low)
```

The result is clipped to stay between `0` and `100`.

Component logic:

| Component | Logic |
| --- | --- |
| Heat risk | Scores maximum temperature above the crop heat threshold. |
| Cold risk | Scores how far minimum temperature falls below the crop cold threshold. |
| Rain risk | Uses rainfall amount when available; otherwise uses precipitation probability. |
| Wind risk | Scores wind speed above the wind risk threshold. |
| Humidity disease risk | Scores humidity above the crop disease-pressure threshold. |

Current formulas:

```text
heat_score = clipped_score(temp_max_c, heat_stress_c, heat_stress_c + 8)

cold_score = clipped_score(cold_stress_c - temp_min_c, 0, 8)

if rain_mm > 0:
    rain_score = clipped_score(rain_mm, 15, 50)
else:
    rain_score = clipped_score(
        rain_probability,
        heavy_rain_probability,
        very_heavy_rain_probability
    )

wind_score = clipped_score(wind_kph, wind_risk_kph, wind_risk_kph + 35)

humidity_disease_score = clipped_score(
    humidity_pct,
    humidity_disease_pct,
    100
)
```

### 6. Composite Risk Model

The final daily risk score is a weighted combination of the component scores:

```text
Composite Risk Score =
30% Rain Risk
25% Heat Risk
15% Cold Risk
15% Wind Risk
15% Humidity Disease Risk
```

Risk level classification:

| Score Range | Risk Level |
| ---: | --- |
| 0-29 | Low |
| 30-54 | Medium |
| 55-74 | High |
| 75-100 | Extreme |

The overall location risk is calculated as the average of the daily risk scores across the forecast window.

### 7. Recommendation Generation

Recommendations are generated from the component scores, not just the final composite score. This makes the guidance more explainable.

Examples:

- High rain risk: avoid fertilizer or pesticide application before rain and check drainage.
- Moderate rain risk: schedule field work during drier windows.
- Heat stress risk: irrigate early morning or late evening when possible.
- Cold stress risk: protect seedlings and sensitive crops.
- Strong wind risk: avoid spraying and secure light structures.
- High humidity disease pressure: scout for fungal symptoms.
- Low weather risk: continue normal farm operations.

The app uses component thresholds of `30` for moderate concern and `55` for high concern.

## Logic Structure

The main application logic lives in `app.py`.

```text
Configuration
|-- Load .env
|-- Read WEATHERAI_API_KEY
|-- Configure WeatherAI base URL

Utility Layer
|-- safe_get()
|   |-- Reads multiple possible response keys safely
|
|-- clipped_score()
|   |-- Converts raw values into bounded 0-100 scores
|
|-- risk_level()
|   |-- Converts numeric scores into Low, Medium, High, or Extreme
|
|-- crop_thresholds()
    |-- Returns generic thresholds plus crop-specific overrides

WeatherAI Layer
|-- fetch_weather()
    |-- Sends authenticated forecast request
    |-- Limits forecast horizon to 7 days
    |-- Raises readable errors for failed API calls

Data Processing Layer
|-- hourly_to_daily()
|   |-- Converts hourly observations into daily analytical features
|
|-- normalize_weather()
    |-- Reads daily, forecast, or days arrays
    |-- Uses hourly fallback where daily values are missing
    |-- Converts numeric fields safely
    |-- Returns a 7-day normalized DataFrame

Risk Modeling Layer
|-- calculate_risk()
|   |-- Computes component scores for every forecast day
|   |-- Computes weighted composite risk score
|   |-- Assigns risk level
|   |-- Finds highest-risk day
|
|-- generate_recommendations()
    |-- Produces action guidance from component risks

Interface Layer
|-- GET /
|   |-- Renders the input form with default Dhaka values
|
|-- POST /
|   |-- Reads submitted form values
|   |-- Fetches forecast data
|   |-- Normalizes weather data
|   |-- Calculates risk
|   |-- Renders summary, worst day, and 7-day table
|
|-- GET /api/risk
|   |-- Returns the same risk model output as JSON
|
|-- GET /health
    |-- Returns application health status
```

## End-to-End Data Flow

```text
User enters location and crop
        |
        v
Flask route receives request
        |
        v
WeatherAI forecast is fetched
        |
        v
Daily and hourly data are normalized
        |
        v
Crop thresholds are selected
        |
        v
Heat, cold, rain, wind, and humidity risks are scored
        |
        v
Weighted composite risk score is calculated
        |
        v
Risk level and recommendations are generated
        |
        v
Results are shown in the web UI or returned as JSON
```

## API Response Logic

The `/api/risk` route accepts:

```text
/api/risk?lat=23.8103&lon=90.4125&crop_type=rice
```

It returns:

- `crop_type`
- `lat`
- `lon`
- `overall_score`
- `overall_level`
- `worst_day`
- `daily`

The `daily` list contains normalized weather metrics, component risk scores, final risk score, risk level, and recommendations for each forecast day.

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file:

```env
WEATHERAI_API_KEY=your_weatherai_api_key_here
```

Run the app:

```bash
python app.py
```

Open the browser at:

```text
http://localhost:5000
```

Check health:

```text
http://localhost:5000/health
```

Example JSON request:

```text
http://localhost:5000/api/risk?lat=23.8103&lon=90.4125&crop_type=rice
```

## Dependencies

```text
Flask
gunicorn
requests
pandas
python-dotenv
```

## Model Assumptions and Limitations

- The model is designed for decision support, not as a substitute for expert agronomic advice.
- Risk thresholds are simplified assumptions and may need calibration for specific crop varieties, soil types, seasons, and local management practices.
- Forecast quality depends on the WeatherAI API response.
- Rainfall scoring prefers measured or forecast rainfall amount when available, then falls back to precipitation probability.
- The app currently scores a maximum 7-day forecast horizon.

## Future Improvements

Potential extensions include:

- Historical weather validation.
- Crop-stage-specific thresholds.
- Soil moisture and irrigation scheduling.
- Flood and drought indicators.
- Location comparison dashboards.
- CSV export from the Flask interface.
- Alerting for high and extreme risk days.
- Calibration with observed crop loss or yield data.

## Conclusion

Farm Weather Risk Scorer demonstrates how forecast data can be transformed into a practical agricultural risk intelligence layer. The core method is intentionally transparent: normalize the forecast, score each weather hazard, combine the scores with readable weights, and generate recommendations tied to the specific risk drivers.
