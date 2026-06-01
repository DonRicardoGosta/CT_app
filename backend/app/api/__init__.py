"""HTTP/WebSocket API.

Deliberately split so fast paths stay fast (REQ-008):

* ``realtime/`` — WebSocket endpoints fed from Kafka (no DB on the hot path).
* ``history/`` — REST endpoints that query PostgreSQL.
* ``config/``  — REST CRUD for settings, API keys, risk and strategy configs.
* ``control/`` — start/stop/list runs (publishes control commands to the worker).
"""
