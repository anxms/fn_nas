import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.core import callback
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
        self._last_status = None  # 跟踪上次状态
    
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
            # 立即更新状态为开启
            self.coordinator.data["system"]["status"] = "on"
            self.coordinator.async_update_listeners()
            self.async_write_ha_state()
        else:
            _LOGGER.warning("无法唤醒系统，未配置MAC地址")
    
    async def async_turn_off(self, **kwargs):
        await self.coordinator.shutdown_system()
        # 立即更新状态为关闭
        self.coordinator.data["system"]["status"] = "off"
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """处理协调器更新"""
        # 获取当前系统状态
        system_data = self.coordinator.data.get("system", {})
        new_status = system_data.get("status", "unknown")
        
        # 如果状态发生变化，强制更新UI
        if self._last_status != new_status:
            self.async_write_ha_state()
        
        # 更新上次状态记录
        self._last_status = new_status
        super()._handle_coordinator_update()
    
    @property
    def extra_state_attributes(self):
        mac = self.config_entry.data.get(CONF_MAC, "未配置")
        return {
            "控制方式": "关机使用命令关机，开机使用网络唤醒",
            "MAC地址": mac,
            "警告": "网络唤醒需要提前配置MAC地址",
            "当前状态": self.coordinator.data["system"].get("status", "未知")
        }