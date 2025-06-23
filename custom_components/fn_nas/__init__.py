import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, DATA_UPDATE_COORDINATOR, PLATFORMS
from .coordinator import FlynasCoordinator, UPSDataUpdateCoordinator
_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    config = {**entry.data, **entry.options}
    
    coordinator = FlynasCoordinator(hass, config)
    await coordinator.async_config_entry_first_refresh()
    
    ups_coordinator = UPSDataUpdateCoordinator(hass, config, coordinator)
    await ups_coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_UPDATE_COORDINATOR: coordinator,
        "ups_coordinator": ups_coordinator
    }
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_entry))
    return True

async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = domain_data[DATA_UPDATE_COORDINATOR]
        ups_coordinator = domain_data["ups_coordinator"]
        
        await coordinator.async_disconnect()
        ups_coordinator.async_shutdown() 
        
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok