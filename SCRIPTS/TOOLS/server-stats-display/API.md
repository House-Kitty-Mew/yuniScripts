# Server Stats Display API

## Overview
Queries the server-stats-collector on UDP 25570 every 3 seconds
and renders a live-updating terminal dashboard with bar charts.

## Displayed Metrics
- **CPU** – Average of all core percentages with bar chart
- **RAM** – Used / Total with bar chart
- **Disk** – Free / Total with bar chart
- **Temp** – Temperature (°F and °C) with bar chart
- **Network** – Total bytes sent and received
- **Processes** – Monitored process aliases

## Commands (internal use)
None. This script runs autonomously as a display-only tool.

## Related Script
Pair with `SERVICES/server-stats-collector` (UDP command port 25570).
