# Simulated People v4 — Dynamic Weather & Geology System

## 1. Philosophy

The world is not static.  Every tick, the sun moves across the sky,
temperatures rise and fall, clouds form and dissipate, wind carries
moisture between regions, and geological processes shape the terrain.

Personas don't just exist in a named area — they exist in a *living
environment* with temperature, humidity, precipitation, and wind that
affects their health, their decisions, and their survival.

All weather emerges from simplified physics — no random() for "is it
raining today".  Temperature comes from solar radiation, moisture from
evaporation, clouds from condensation, wind from pressure gradients.

---

## 2. Architecture

### Scales
| Tick | Duration | Updates |
|------|----------|---------|
| Weather tick | 1 game-hour | Temperature, humidity, clouds, wind, precipitation |
| Daily tick | 24 game-hours | Geology (erosion, soil formation), seasonal progression |
| Persona tick | Sub-hourly | Health decay, behavior decisions |

### Grid
Instead of a full 256×256 cell grid, weather operates at the **area
level** (13+ named areas across 4 regions).  Each area has:
- A central weather state (temperature, humidity, etc.)
- Connections to neighboring areas (wind/moisture advection)
- A biome type (determines baseline climate)
- Elevation (affects temperature lapse rate)
- A geographic type (land, ocean, mountain, etc.)

---

## 3. Weather State (Per Area)

| Variable | Range | Description |
|----------|-------|-------------|
| temperature | -20 to 60°C | Current air temperature |
| humidity | 0-100% | Relative humidity |
| cloud_cover | 0-1 | Fraction of sky covered |
| precipitation_mm | 0-50 | Hourly rainfall mm |
| wind_speed | 0-30 | Wind speed (m/s) |
| wind_direction | 0-360 | Degrees from north |
| pressure | 980-1040 hPa | Atmospheric pressure |
| is_raining | bool | Currently raining? |
| is_snowing | bool | Snowing (when temp < 0°C) |

---

## 4. Weather Update (Per Tick)

### 4.1 Solar Radiation
```
solar_angle = f(latitude, hour, day_of_year)
radiation = 1000 * sin(solar_angle)
if solar_angle < 0: radiation = 0  # night
solar_radiation = radiation * (1 - 0.6 * cloud_cover)
```

### 4.2 Temperature
```
new_temp = old_temp + heating_from_sun + cooling_from_clouds
           + wind_advection + elevation_lapse + diurnal_drift
```

### 4.3 Humidity & Precipitation
```
evaporation = f(wind, temperature, water_proximity)
humidity += evaporation
if humidity > 100:  # supersaturation
    excess = humidity - 100
    cloud_cover += excess * 0.01
    humidity = 100
    # latent heat release warms air slightly
    
if cloud_cover > 0.8 and temperature > -5:
    precipitation_mm = cloud_cover * 0.5
    cloud_cover -= 0.1  # rain removes clouds
```

### 4.4 Wind (Pressure-Gradient)
```
pressure = 1013 * (1 - elevation * 0.0001) + temperature_factor
wind_u = -dP/dx / density + coriolis + friction
wind_v = -dP/dy / density + coriolis + friction
wind_speed = sqrt(wind_u² + wind_v²)
```

### 4.5 Advection Between Areas
```
temperature_advection = sum((neighbor_temp - this_temp) * wind_factor)
humidity_advection = sum((neighbor_humidity - this_humidity) * wind_factor)
cloud_advection = sum((neighbor_cloud - this_cloud) * wind_factor)
```

---

## 5. Geological Processes (Daily)

### 5.1 Erosion (Simplified)
```
runoff = precipitation_mm * 0.3  # 30% becomes runoff
if slope_to_lowest_neighbor > 0:
    erosion = runoff^0.5 * slope * 0.001
    soil_depth -= erosion
    downstream_cell.soil_depth += erosion
```

### 5.2 Soil Formation
```
weathering_rate = 0.01 * temperature_factor * moisture_factor
soil_depth += weathering_rate
soil_fertility = f(soil_depth, organic_matter, temperature)
```

### 5.3 Vegetation Growth
```
if temperature > 5°C and precipitation > 20mm/month:
    vegetation_density += 0.001
else:
    vegetation_density -= 0.0005  # drought/frost kills plants
```

---

## 6. Weather Extremes (Emergent)

### 6.1 Storms
- Form when: 2+ adjacent areas have pressure < 990 hPa
- Effects: wind_speed ×3, precipitation ×4, cloud_cover = 1.0
- Duration: 2-6 hours
- Personas stay home (no market activity)

### 6.2 Heatwaves
- Form when: 3+ consecutive days with temp > 35°C
- Effects: dehydration ×2, food decay ×1.5, energy drain ×2
- Duration: 2-7 days
- Personas seek water/move to cooler biomes

### 6.3 Blizzards
- Form when: temp < -10°C + high wind + precipitation
- Effects: hypothermia risk ×3, movement impossible
- Duration: 1-3 days
- Personas shelter, demand for food/fuel spikes

### 6.4 Droughts
- Form when: 10+ days with <1mm rain
- Effects: food decay ×2, farming income = 0, water scarcity
- Duration: 7-30 days
- Farmers go bankrupt, food prices spike

---

## 7. Connection to Health

| Weather Condition | Health Effect |
|-------------------|---------------|
| Temp < -5°C | Temperature stat drops toward ambient at rate × wind |
| Temp > 35°C | Temperature rises, dehydration accelerates ×1.5 |
| Rain (wetness) | No direct effect yet (future: cold+wet = immune drop) |
| High altitude (elevation) | Oxygen reduction if modeled (future) |
| Wind chill | Temperature stat drops faster |

### Health Integration Code
```python
# In health tick, apply area weather:
area = get_persona_area(persona_uuid)
weather = get_area_weather(area["area_uuid"])

# Temperature drifts toward ambient
ambient = weather["temperature"]
wind = weather.get("wind_speed", 0)
wind_chill = max(0, ambient - wind * 0.3) if ambient < 15 else min(ambient, ambient + wind * 0.1)
drift_rate = 0.1 + wind * 0.01
new_temp = health.temperature + (ambient - health.temperature) * drift_rate
```

---

## 8. Seasonal Cycle

The world has a 365-day year with:
- **Spring** (days 60-151): Warming, increased rainfall
- **Summer** (days 152-243): Hot, variable rainfall
- **Autumn** (days 244-334): Cooling, decreased rainfall  
- **Winter** (days 335-59, 0-59): Cold, snow at high elevations/latitudes

Season affects:
- Base temperature offset (±15°C from annual mean)
- Precipitation patterns
- Vegetation growth rates
- Wind patterns (prevailing direction shifts)

---

## 9. Database

### `ext_sp_weather` (new table)
| Column | Type | Description |
|--------|------|-------------|
| area_uuid | TEXT FK | References ext_sp_world_areas |
| temperature | REAL | °C |
| humidity | REAL | 0-100% |
| cloud_cover | REAL | 0-1 |
| precipitation_mm | REAL | Hourly mm |
| wind_speed | REAL | m/s |
| wind_direction | REAL | degrees |
| pressure | REAL | hPa |
| is_raining | INTEGER | bool |
| is_snowing | INTEGER | bool |
| updated_at | TEXT | ISO timestamp |

---

## 10. Implementation Files

| File | Purpose |
|------|---------|
| `sp_weather.py` | Weather engine — tick-by-tick physics |
| `sp_geology.py` | Geological processes (daily) |
| `sp_seasons.py` | Seasonal cycle and climate baselines |
| `sp_database.py` | Added `ext_sp_weather` table |
| `sp_health.py` | Updated to use weather data in temperature/dryness |
| `sp_behavior.py` | Updated to consider weather for movement/activity |
| `sp_movement.py` | Updated: weather affects migration patterns |
| `__init__.py` | Process weather in tick loop |
| `config.json` | Added weather tuning parameters |
