"""Constants for the Eufy RoboVac integration."""

DOMAIN = "robovac"
CONF_VACS = "vacuums"
CONF_AUTODISCOVERY = "autodiscovery"
REFRESH_RATE = 60
PING_RATE = 10
TIMEOUT = 5

# Dispatcher signal fired by RoboVacEntity on every DPS update that may
# contain map data.  Full signal name: f"{SIGNAL_MAP_UPDATE}_{unique_id}"
# Payload: the full tuyastatus dict (str → Any).
SIGNAL_MAP_UPDATE = f"{DOMAIN}_map_update"
