import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, DATA_UPDATE_COORDINATOR, DEVICE_ID_NAS

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id][DATA_UPDATE_COORDINATOR]
    async_add_entities([RebootButton(coordinator)])

class RebootButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "重启"
    _attr_unique_id = "flynas_reboot"
    _attr_entity_category = EntityCategory.CONFIG
    
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统",
            "manufacturer": "飞牛",
            "model": "飞牛NAS"
        }
    
    async def async_press(self):
        await self.coordinator.reboot_system()
        self.async_write_ha_state()
        
    @property
    def extra_state_attributes(self):
        return {
            "提示": "按下此按钮将重启飞牛NAS系统"
        }