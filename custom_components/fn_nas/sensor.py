import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature
from .const import (
    DOMAIN, HDD_TEMP, HDD_HEALTH, SYSTEM_INFO, ICON_DISK, 
    ICON_TEMPERATURE, ICON_HEALTH, ATTR_DISK_MODEL, ATTR_SERIAL_NO,
    ATTR_POWER_ON_HOURS, ATTR_TOTAL_CAPACITY, ATTR_HEALTH_STATUS,
    DEVICE_ID_NAS
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    
    entities = []
    
    # 添加硬盘传感器
    for disk in coordinator.data.get("disks", []):
        # 温度传感器
        entities.append(
            DiskSensor(
                coordinator, 
                disk["device"], 
                HDD_TEMP,
                f"硬盘 {disk.get('model', '未知')} 温度",
                f"{disk['device']}_temperature",
                UnitOfTemperature.CELSIUS,
                ICON_TEMPERATURE,
                disk
            )
        )
        
        # 健康状态传感器
        entities.append(
            DiskSensor(
                coordinator, 
                disk["device"], 
                HDD_HEALTH,
                f"硬盘 {disk.get('model', '未知')} 健康状态",
                f"{disk['device']}_health",
                None,
                ICON_HEALTH,
                disk
            )
        )
    
    # 添加系统信息传感器
    entities.append(
        SystemSensor(
            coordinator,
            "系统状态",
            "system_status",
            None,
            "mdi:server",
        )
    )
    
    # 添加CPU温度传感器
    entities.append(
        CPUTempSensor(
            coordinator,
            "CPU温度",
            "cpu_temperature",
            UnitOfTemperature.CELSIUS,
            "mdi:thermometer",
        )
    )
    
    # 添加主板温度传感器
    """ entities.append(
        MoboTempSensor(
            coordinator,
            "主板温度",
            "motherboard_temperature",
            UnitOfTemperature.CELSIUS,
            "mdi:thermometer",
        )
    ) """
    
    async_add_entities(entities)

class DiskSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_id, sensor_type, name, unique_id, unit, icon, disk_info):
        super().__init__(coordinator)
        self.device_id = device_id
        self.sensor_type = sensor_type
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self.disk_info = disk_info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"disk_{device_id}")},
            "name": disk_info.get("model", "未知硬盘"),
            "manufacturer": "硬盘设备",
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }
    
    @property
    def native_value(self):
        for disk in self.coordinator.data.get("disks", []):
            if disk["device"] == self.device_id:
                if self.sensor_type == HDD_TEMP:
                    temp = disk.get("temperature", "未知")
                    # 提取数字部分
                    if isinstance(temp, str) and "°C" in temp:
                        return temp.replace("°C", "").strip()
                    return temp
                elif self.sensor_type == HDD_HEALTH:
                    return disk.get("health", "未知")
        return "未知"
    
    @property
    def device_class(self):
        if self.sensor_type == HDD_TEMP:
            return SensorDeviceClass.TEMPERATURE
        return None
    
    @property
    def extra_state_attributes(self):
        return {
            ATTR_DISK_MODEL: self.disk_info.get("model", "未知"),
            ATTR_SERIAL_NO: self.disk_info.get("serial", "未知"),
            ATTR_POWER_ON_HOURS: self.disk_info.get("power_on_hours", "未知"),
            ATTR_TOTAL_CAPACITY: self.disk_info.get("capacity", "未知"),
            ATTR_HEALTH_STATUS: self.disk_info.get("health", "未知"),
            "设备ID": self.device_id
        }

class SystemSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, unique_id, unit, icon):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统监控",
            "manufacturer": "飞牛"
        }
    
    @property
    def native_value(self):
        system_data = self.coordinator.data.get("system", {})
        # 直接获取原始运行时间（秒数）
        uptime_seconds = system_data.get("uptime_seconds", 0)
        status = system_data.get("status", "unknown")
        
        if status == "on":
            try:
                hours = float(uptime_seconds) / 3600
                return f"运行中 ({hours:.1f}小时)"
            except (ValueError, TypeError):
                return "运行中"
        elif status == "off":
            return "关机"
        return "状态未知"
    
    @property
    def extra_state_attributes(self):
        system_data = self.coordinator.data.get("system", {})
        return {
            # 显示格式化后的运行时间
            "运行时间": system_data.get("uptime", "未知"),
            "系统状态": system_data.get("status", "unknown"),
            "主机地址": self.coordinator.host
        }

class CPUTempSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, unique_id, unit, icon):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统监控",
            "manufacturer": "飞牛"
        }
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
    
    @property
    def native_value(self):
        system_data = self.coordinator.data.get("system", {})
        temp_str = system_data.get("cpu_temperature", "未知")
        
        # 提取温度数值
        if "°C" in temp_str:
            try:
                return float(temp_str.replace("°C", "").strip())
            except:
                return None
        return None

class MoboTempSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, unique_id, unit, icon):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统监控",
            "manufacturer": "飞牛"
        }
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
    
    @property
    def native_value(self):
        system_data = self.coordinator.data.get("system", {})
        temp_str = system_data.get("motherboard_temperature", "未知")
        
        # 提取温度数值
        if "°C" in temp_str:
            try:
                return float(temp_str.replace("°C", "").strip())
            except:
                return None
        return None