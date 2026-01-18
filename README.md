🌐 Lab Monitoring Server: Core Logic & Infrastructure
The server  has been refactored into a high-performance, stateless HTTP engine. Below is the technical description of the current architecture:

1. Presence & Heartbeat Engine (/api/heartbeat)
Strategy: Replaced all legacy WebSocket listeners with a high-frequency RESTful heartbeat.
Registration: Seamlessly registers new hardware via unique UUIDs. If a device is already registered, it instantly synchronizes the cpu_score and last_seen timestamps.
Daily Reset Logic: Monitors the date of incoming pulses. If a pulse arrives on a newer date than the last_seen record, the server automatically archives the previous period's metrics to the history table before initializing the new day.
2. Intelligence & Automation (Background monitor)
Offline Sentinel: A dedicated background thread executes every 20 seconds. It identifies any "Online" devices that haven't sent a pulse in over 40 seconds and forcibly marks them as offline in the database.
Auto-Pruning (24h): Automatically wipes transient session/heartbeat data older than 24 hours to keep the main 
devices
 table lean and fast.
Archive Life-cycle (7-Day): Manages the device_daily_history table. It ensures only the most recent 7 days of daily summaries (Start, Last Seen, Score, Runtime) are retained, deleting anything older than a week.
3. Data Integration & Analytics
Location Intelligence: Aggregates real-time counts of online vs. offline units per City and Laboratory for the dashboard statistics.
Session Reconstruction: Calculates precision "Runtime" by comparing the daily boot-up time (today_start_time) against the most recent activity pulse.
4. Deployment Architecture
Environment: Optimized for Render/Gunicorn with gevent monkey-patching for concurrent pulse handling.
Storage: Direct integration with Supabase for persistent hardware identity and encrypted score accumulation.
