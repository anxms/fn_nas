import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature
from .const import (
    DOMAIN, HDD_TEMP, HDD_HEALTH, HDD_STATUS, SYSTEM_INFO, ICON_DISK, 
    ICON_TEMPERATURE, ICON_HEALTH, ATTR_DISK_MODEL, ATTR_SERIAL_NO,
    ATTR_POWER_ON_HOURS, ATTR_TOTAL_CAPACITY, ATTR_HEALTH_STATUS,
    DEVICE_ID_NAS, DATA_UPDATE_COORDINATOR
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    domain_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = domain_data[DATA_UPDATE_COORDINATOR]
    ups_coordinator = domain_data["ups_coordinator"]
    
    entities = []
    existing_ids = set()
    
    # 添加硬盘传感器
    for disk in coordinator.data.get("disks", []):
        # 温度传感器
        temp_uid = f"{config_entry.entry_id}_{disk['device']}_temperature"
        if temp_uid not in existing_ids:
            entities.append(
                DiskSensor(
                    coordinator, 
                    disk["device"], 
                    HDD_TEMP,
                    f"硬盘 {disk.get('model', '未知')} 温度",
                    temp_uid,
                    UnitOfTemperature.CELSIUS,
                    ICON_TEMPERATURE,
                    disk
                )
            )
            existing_ids.add(temp_uid)
        
        # 健康状态传感器
        health_uid = f"{config_entry.entry_id}_{disk['device']}_health"
        if health_uid not in existing_ids:
            entities.append(
                DiskSensor(
                    coordinator, 
                    disk["device"], 
                    HDD_HEALTH,
                    f"硬盘 {disk.get('model', '未知')} 健康状态",
                    health_uid,
                    None,
                    ICON_HEALTH,
                    disk
                )
            )
            existing_ids.add(health_uid)
        
        # 硬盘状态传感器
        status_uid = f"{config_entry.entry_id}_{disk['device']}_status"
        if status_uid not in existing_ids:
            entities.append(
                DiskSensor(
                    coordinator, 
                    disk["device"], 
                    HDD_STATUS,
                    f"硬盘 {disk.get('model', '未知')} 状态",
                    status_uid,
                    None,
                    ICON_DISK,
                    disk
                )
            )
            existing_ids.add(status_uid)
    
    # 添加系统信息传感器
    system_uid = f"{config_entry.entry_id}_system_status"
    if system_uid not in existing_ids:
        entities.append(
            SystemSensor(
                coordinator,
                "系统状态",
                system_uid,
                None,
                "mdi:server",
            )
        )
        existing_ids.add(system_uid)
    
    # 添加CPU温度传感器
    cpu_temp_uid = f"{config_entry.entry_id}_cpu_temperature"
    if cpu_temp_uid not in existing_ids:
        entities.append(
            CPUTempSensor(
                coordinator,
                "CPU温度",
                cpu_temp_uid,
                UnitOfTemperature.CELSIUS,
                "mdi:thermometer",
            )
        )
        existing_ids.add(cpu_temp_uid)
    
    # 添加主板温度传感器
    mobo_temp_uid = f"{config_entry.entry_id}_motherboard_temperature"
    if mobo_temp_uid not in existing_ids:
        entities.append(
            MoboTempSensor(
                coordinator,
                "主板温度",
                mobo_temp_uid,
                UnitOfTemperature.CELSIUS,
                "mdi:thermometer",
            )
        )
        existing_ids.add(mobo_temp_uid)

    # 添加虚拟机状态传感器
    if "vms" in coordinator.data:
        for vm in coordinator.data["vms"]:
            vm_uid = f"{config_entry.entry_id}_flynas_vm_{vm['name']}_status"
            if vm_uid not in existing_ids:
                entities.append(
                    VMStatusSensor(
                        coordinator, 
                        vm["name"],
                        vm.get("title", vm["name"]),
                        config_entry.entry_id
                    )
                )
                existing_ids.add(vm_uid)

    # 添加UPS传感器（使用UPS协调器）
    if ups_coordinator.data:  # 检查是否有UPS数据
        ups_data = ups_coordinator.data
        
        # UPS电池电量传感器
        ups_battery_uid = f"{config_entry.entry_id}_ups_battery"
        if ups_battery_uid not in existing_ids:
            entities.append(
                UPSSensor(
                    ups_coordinator,  # 使用UPS协调器
                    "UPS电池电量",
                    ups_battery_uid,
                    "%",
                    "mdi:battery",
                    "battery_level",
                    device_class=SensorDeviceClass.BATTERY,
                    state_class=SensorStateClass.MEASUREMENT
                )
            )
            existing_ids.add(ups_battery_uid)
        
        # UPS剩余时间传感器
        ups_runtime_uid = f"{config_entry.entry_id}_ups_runtime"
        if ups_runtime_uid not in existing_ids:
            entities.append(
                UPSSensor(
                    ups_coordinator,  # 使用UPS协调器
                    "UPS剩余时间",
                    ups_runtime_uid,
                    "分钟",
                    "mdi:clock",
                    "runtime_remaining",
                    state_class=SensorStateClass.MEASUREMENT
                )
            )
            existing_ids.add(ups_runtime_uid)
        
        # UPS输出电压传感器
        ups_output_voltage_uid = f"{config_entry.entry_id}_ups_output_voltage"
        if ups_output_voltage_uid not in existing_ids:
            entities.append(
                UPSSensor(
                    ups_coordinator,  # 使用UPS协调器
                    "UPS输出电压",
                    ups_output_voltage_uid,
                    "V",
                    "mdi:lightning-bolt-outline",
                    "output_voltage",
                    device_class=SensorDeviceClass.VOLTAGE,
                    state_class=SensorStateClass.MEASUREMENT
                )
            )
            existing_ids.add(ups_output_voltage_uid)
        
        # UPS负载传感器
        ups_load_uid = f"{config_entry.entry_id}_ups_load"
        if ups_load_uid not in existing_ids:
            entities.append(
                UPSSensor(
                    ups_coordinator,  # 使用UPS协调器
                    "UPS负载",
                    ups_load_uid,
                    "%",
                    "mdi:gauge",
                    "load_percent",
                    state_class=SensorStateClass.MEASUREMENT
                )
            )
            existing_ids.add(ups_load_uid)
        
        # UPS型号传感器
        ups_model_uid = f"{config_entry.entry_id}_ups_model"
        if ups_model_uid not in existing_ids:
            entities.append(
                UPSSensor(
                    ups_coordinator,  # 使用UPS协调器
                    "UPS型号",
                    ups_model_uid,
                    None,
                    "mdi:information",
                    "model"
                )
            )
            existing_ids.add(ups_model_uid)
        
        # UPS状态传感器
        ups_status_uid = f"{config_entry.entry_id}_ups_status"
        if ups_status_uid not in existing_ids:
            entities.append(
                UPSSensor(
                    ups_coordinator,  # 使用UPS协调器
                    "UPS状态",
                    ups_status_uid,
                    None,
                    "mdi:power-plug",
                    "status"
                )
            )
            existing_ids.add(ups_status_uid)

        if coordinator.data.get("docker_containers") and coordinator.enable_docker:
            for container in coordinator.data["docker_containers"]:
                safe_name = container["name"].replace(" ", "_").replace("/", "_")
                sensor_uid = f"{config_entry.entry_id}_docker_{safe_name}_status"
                if sensor_uid not in existing_ids:
                    entities.append(
                        DockerContainerStatusSensor(
                            coordinator, 
                            container["name"],
                            safe_name,
                            config_entry.entry_id
                        )
                    )
                    existing_ids.add(sensor_uid)
    
    # 添加剩余内存传感器
    mem_available_uid = f"{config_entry.entry_id}_memory_available"
    if mem_available_uid not in existing_ids:
        entities.append(
            MemoryAvailableSensor(
                coordinator,
                "可用内存",
                mem_available_uid,
                "GB",
                "mdi:memory"
            )
        )
        existing_ids.add(mem_available_uid)
    
    # 添加存储卷的剩余容量传感器（每个卷一个）
    system_data = coordinator.data.get("system", {})
    volumes = system_data.get("volumes", {})
    for mount_point in volumes:
        # 创建剩余容量传感器
        vol_avail_uid = f"{config_entry.entry_id}_{mount_point.replace('/', '_')}_available"
        if vol_avail_uid not in existing_ids:
            entities.append(
                VolumeAvailableSensor(
                    coordinator,
                    f"{mount_point} 可用空间",
                    vol_avail_uid,
                    "mdi:harddisk",
                    mount_point
                )
            )
            existing_ids.add(vol_avail_uid)

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
                    temp = disk.get("temperature")
                    if temp is None or temp == "未知" or temp == "未检测":
                        return None
                    if isinstance(temp, str):
                        try:
                            if "°C" in temp:
                                return float(temp.replace("°C", "").strip())
                            return float(temp)
                        except ValueError:
                            return None
                    elif isinstance(temp, (int, float)):
                        return temp
                    return None
                elif self.sensor_type == HDD_HEALTH:
                    health = disk.get("health", "未知")
                    if health == "未检测":
                        return "未检测"
                    return health if health != "未知" else "未知状态"
                elif self.sensor_type == HDD_STATUS:
                    return disk.get("status", "未知")
        return None
    
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
            "设备ID": self.device_id,
            "状态": self.disk_info.get("status", "未知")
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
        self._last_uptime = None
    
    @property
    def native_value(self):
        system_data = self.coordinator.data.get("system", {})
        status = system_data.get("status", "unknown")
        
        if status == "off":
            return "离线"
        if status == "rebooting":
            return "重启中"
        if status == "unknown":
            return "状态未知"
        
        try:
            uptime_seconds = system_data.get("uptime_seconds", 0)
            if self._last_uptime == uptime_seconds:
                return self._last_value
            
            hours = float(uptime_seconds) / 3600
            value = f"已运行 {hours:.1f}小时"
            self._last_value = value
            self._last_uptime = uptime_seconds
            return value
        except (ValueError, TypeError):
            return "运行中"
    
    @property
    def extra_state_attributes(self):
        system_data = self.coordinator.data.get("system", {})
        return {
            "运行时间": system_data.get("uptime", "未知"),
            "系统状态": system_data.get("status", "unknown"),
            "主机地址": self.coordinator.host,
            "CPU温度": system_data.get("cpu_temperature", "未知"),
            "主板温度": system_data.get("motherboard_temperature", "未知")
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
        
        if system_data.get("status") == "off":
            return None
        
        if temp_str is None or temp_str == "未知":
            return None
        
        if isinstance(temp_str, (int, float)):
            return temp_str
            
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
        
        if system_data.get("status") == "off":
            return None
        
        if temp_str is None or temp_str == "未知":
            return None
        
        if isinstance(temp_str, (int, float)):
            return temp_str
            
        try:
            cleaned = temp_str.lower().replace('°c', '').replace('c', '').strip()
            return float(cleaned)
        except (ValueError, TypeError) as e:
            _LOGGER.warning("主板温度解析失败: 原始值='%s', 错误: %s", temp_str, str(e))
            return None

class UPSSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, unique_id, unit, icon, data_key, device_class=None, state_class=None):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self.data_key = data_key
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "flynas_ups")},
            "name": "飞牛NAS UPS",
            "manufacturer": "UPS设备",
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }
        
        # 设置设备类和状态类（如果提供）
        if device_class:
            self._attr_device_class = device_class
        if state_class:
            self._attr_state_class = state_class
    
    @property
    def native_value(self):
        return self.coordinator.data.get(self.data_key)  # 直接使用协调器的数据
    
    @property
    def extra_state_attributes(self):
        attributes = {
            "最后更新时间": self.coordinator.data.get("last_update", "未知"),
            "UPS类型": self.coordinator.data.get("ups_type", "未知")
        }
        
        # 添加原始字符串值（如果存在）
        if f"{self.data_key}_str" in self.coordinator.data:
            attributes["原始值"] = self.coordinator.data[f"{self.data_key}_str"]
        
        return attributes

class VMStatusSensor(CoordinatorEntity, SensorEntity):
    """虚拟机状态传感器"""
    
    def __init__(self, coordinator, vm_name, vm_title, entry_id):
        super().__init__(coordinator)
        self.vm_name = vm_name
        self.vm_title = vm_title
        self._attr_name = f"{vm_title} 状态"
        self._attr_unique_id = f"{entry_id}_flynas_vm_{vm_name}_status"  # 使用entry_id确保唯一性
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"vm_{vm_name}")},
            "name": vm_title,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }
    
    @property
    def native_value(self):
        """返回虚拟机状态"""
        for vm in self.coordinator.data.get("vms", []):
            if vm["name"] == self.vm_name:
                # 将状态转换为中文
                state_map = {
                    "running": "运行中",
                    "shut off": "已关闭",
                    "paused": "已暂停",
                    "rebooting": "重启中",
                    "crashed": "崩溃"
                }
                return state_map.get(vm["state"], vm["state"])
        return "未知"
    
    @property
    def icon(self):
        """根据状态返回图标"""
        for vm in self.coordinator.data.get("vms", []):
            if vm["name"] == self.vm_name:
                if vm["state"] == "running":
                    return "mdi:server"
                elif vm["state"] == "shut off":
                    return "mdi:server-off"
                elif vm["state"] == "rebooting":
                    return "mdi:server-security"
        return "mdi:server"

# 添加DockerContainerStatusSensor类
class DockerContainerStatusSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, container_name, safe_name, entry_id):
        super().__init__(coordinator)
        self.container_name = container_name
        self._attr_name = f"{container_name} 状态"
        self._attr_unique_id = f"{entry_id}_docker_{safe_name}_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"docker_{safe_name}")},
            "name": container_name,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }

    @property
    def native_value(self):
        for container in self.coordinator.data.get("docker_containers", []):
            if container["name"] == self.container_name:
                # 状态映射为中文
                status_map = {
                    "running": "运行中",
                    "exited": "已停止",
                    "paused": "已暂停",
                    "restarting": "重启中",
                    "dead": "死亡"
                }
                return status_map.get(container["status"], container["status"])
        return "未知"

class MemoryAvailableSensor(CoordinatorEntity, SensorEntity):
    """剩余内存传感器（包含总内存和已用内存作为属性）"""
    
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
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self):
        """返回可用内存（GB）"""
        system_data = self.coordinator.data.get("system", {})
        mem_available = system_data.get("memory_available")
        
        if mem_available is None or mem_available == "未知":
            return None
        
        try:
            # 将字节转换为GB
            return round(float(mem_available) / (1024 ** 3), 2)
        except (TypeError, ValueError):
            return None
    
    @property
    def extra_state_attributes(self):
        """返回总内存和已用内存（GB）以及原始字节值"""
        system_data = self.coordinator.data.get("system", {})
        mem_total = system_data.get("memory_total")
        mem_used = system_data.get("memory_used")
        mem_available = system_data.get("memory_available")
        
        # 转换为GB
        try:
            mem_total_gb = round(float(mem_total) / (1024 ** 3), 2) if mem_total and mem_total != "未知" else None
        except:
            mem_total_gb = None
            
        try:
            mem_used_gb = round(float(mem_used) / (1024 ** 3), 2) if mem_used and mem_used != "未知" else None
        except:
            mem_used_gb = None
            
        return {
            "总内存 (GB)": mem_total_gb,
            "已用内存 (GB)": mem_used_gb
        }

class VolumeAvailableSensor(CoordinatorEntity, SensorEntity):
    """存储卷剩余容量传感器（包含总容量和已用容量作为属性）"""
    
    def __init__(self, coordinator, name, unique_id, icon, mount_point):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self.mount_point = mount_point
        
        # 设备信息，归属到飞牛NAS系统
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统监控",
            "manufacturer": "飞牛"
        }
        
        self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self):
        """返回剩余容量（数值）"""
        system_data = self.coordinator.data.get("system", {})
        volumes = system_data.get("volumes", {})
        vol_info = volumes.get(self.mount_point, {})
        
        avail_str = vol_info.get("available", "未知")
        if avail_str == "未知":
            return None
        
        try:
            numeric_part = ''.join(filter(lambda x: x.isdigit() or x == '.', avail_str))
            return float(numeric_part)
        except (TypeError, ValueError):
            return None
    
    @property
    def native_unit_of_measurement(self):
        """动态返回单位"""
        system_data = self.coordinator.data.get("system", {})
        volumes = system_data.get("volumes", {})
        vol_info = volumes.get(self.mount_point, {})
        
        avail_str = vol_info.get("available", "")
        if avail_str.endswith("T") or avail_str.endswith("Ti"):
            return "TB"
        elif avail_str.endswith("G") or avail_str.endswith("Gi"):
            return "GB"
        elif avail_str.endswith("M") or avail_str.endswith("Mi"):
            return "MB"
        else:
            return None  # 未知单位
    
    @property
    def extra_state_attributes(self):
        system_data = self.coordinator.data.get("system", {})
        volumes = system_data.get("volumes", {})
        vol_info = volumes.get(self.mount_point, {})
        
        return {
            "挂载点": self.mount_point,
            "文件系统": vol_info.get("filesystem", "未知"),
            "总容量": vol_info.get("size", "未知"),
            "已用容量": vol_info.get("used", "未知"),
            "使用率": vol_info.get("use_percent", "未知")
        }
        
        
        return attributes