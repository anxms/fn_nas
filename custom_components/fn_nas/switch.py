import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, DATA_UPDATE_COORDINATOR, CONF_MAC, DEVICE_ID_NAS

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id][DATA_UPDATE_COORDINATOR]
    async_add_entities([PowerSwitch(coordinator, config_entry)])

class PowerSwitch(CoordinatorEntity, SwitchEntity):
    _attr_name = "电源"
    _attr_unique_id = "flynas_power"
    _attr_entity_category = EntityCategory.CONFIG
    
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统",
            "manufacturer": "飞牛",
            "model": "飞牛NAS"
        }
    
    @property
    def is_on(self):
        system_data = self.coordinator.data.get("system", {})
        return system_data.get("status") == "on"
    
    async def async_turn_on(self, **kwargs):
        mac = self.config_entry.data.get(CONF_MAC)
        if mac:
            await self.hass.services.async_call(
                'wake_on_lan',
                'send_magic_packet',
                {'mac': mac}
            )
            # 设置状态为开启
            self.coordinator.data["system"]["status"] = "on"
            self.coordinator.async_set_updated_data(self.coordinator.data)
        else:
            _LOGGER.warning("无法唤醒系统，未配置MAC地址")
    
    async def async_turn_off(self, **kwargs):
        await self.coordinator.shutdown_system()
        
    @property
    def extra_state_attributes(self):
        mac = self.config_entry.data.get(CONF_MAC, "未配置")
        return {
            "控制方式": "关机使用命令关机，开机使用网络唤醒",
            "MAC地址": mac,
            "警告": "网络唤醒需要提前配置MAC地址"
        }