import logging
from homeassistant.core import callback
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, DATA_UPDATE_COORDINATOR, CONF_MAC, DEVICE_ID_NAS

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    domain_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = domain_data[DATA_UPDATE_COORDINATOR]
    
    entities = []
    entities.append(PowerSwitch(coordinator, config_entry))
    
    if "vms" in coordinator.data:
        for vm in coordinator.data["vms"]:
            entities.append(
                VMSwitch(
                    coordinator, 
                    vm["name"],
                    vm.get("title", vm["name"])
                )
            )

    if coordinator.data.get("docker_containers") and coordinator.enable_docker:
        for container in coordinator.data["docker_containers"]:
            # 使用容器名称作为唯一ID的一部分
            safe_name = container["name"].replace(" ", "_").replace("/", "_")
            entities.append(
                DockerContainerSwitch(
                    coordinator, 
                    container["name"],
                    safe_name
                )
            )

    async_add_entities(entities)

class PowerSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_name = "电源"
        self._attr_unique_id = "flynas_power"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统",
            "manufacturer": "飞牛",
            "model": "飞牛NAS"
        }
        self._last_status = None
    
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
            self.coordinator.data["system"]["status"] = "on"
            self.coordinator.async_update_listeners()
            self.async_write_ha_state()
        else:
            _LOGGER.warning("无法唤醒系统，未配置MAC地址")
    
    async def async_turn_off(self, **kwargs):
        await self.coordinator.shutdown_system()
        self.coordinator.data["system"]["status"] = "off"
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()
    
    @callback
    def _handle_coordinator_update(self) -> None:
        system_data = self.coordinator.data.get("system", {})
        new_status = system_data.get("status", "unknown")
        
        if self._last_status != new_status:
            self.async_write_ha_state()
        
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

class VMSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator, vm_name, vm_title):
        super().__init__(coordinator)
        self.vm_name = vm_name
        self.vm_title = vm_title
        self._attr_name = f"{vm_title} 电源"
        self._attr_unique_id = f"flynas_vm_{vm_name}_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"vm_{vm_name}")},
            "name": vm_title,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }
        self.vm_manager = coordinator.vm_manager if hasattr(coordinator, 'vm_manager') else None
    
    @property
    def is_on(self):
        for vm in self.coordinator.data.get("vms", []):
            if vm["name"] == self.vm_name:
                return vm["state"] == "running"
        return False
    
    async def async_turn_on(self, **kwargs):
        if not self.vm_manager:
            _LOGGER.error("vm_manager不可用，无法启动虚拟机 %s", self.vm_name)
            return
            
        try:
            success = await self.vm_manager.control_vm(self.vm_name, "start")
            if success:
                for vm in self.coordinator.data.get("vms", []):
                    if vm["name"] == self.vm_name:
                        vm["state"] = "running"
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("启动虚拟机时出错: %s", str(e), exc_info=True)
    
    async def async_turn_off(self, **kwargs):
        if not self.vm_manager:
            _LOGGER.error("vm_manager不可用，无法关闭虚拟机 %s", self.vm_name)
            return
            
        try:
            success = await self.vm_manager.control_vm(self.vm_name, "shutdown")
            if success:
                for vm in self.coordinator.data.get("vms", []):
                    if vm["name"] == self.vm_name:
                        vm["state"] = "shut off"
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("关闭虚拟机时出错: %s", str(e), exc_info=True)
    
    @property
    def extra_state_attributes(self):
        for vm in self.coordinator.data.get("vms", []):
            if vm["name"] == self.vm_name:
                return {
                    "虚拟机ID": vm["id"],
                    "原始状态": vm["state"]
                }
        return {}

# 添加DockerContainerSwitch类
class DockerContainerSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator, container_name, safe_name):
        super().__init__(coordinator)
        self.container_name = container_name
        self._attr_name = f"{container_name} 容器"
        self._attr_unique_id = f"docker_{safe_name}_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"docker_{safe_name}")},
            "name": container_name,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }

    @property
    def is_on(self):
        for container in self.coordinator.data.get("docker_containers", []):
            if container["name"] == self.container_name:
                return container["status"] == "running"
        return False

    async def async_turn_on(self, **kwargs):
        if self.coordinator.docker_manager:
            success = await self.coordinator.docker_manager.control_container(self.container_name, "start")
            if success:
                # 更新状态
                for container in self.coordinator.data.get("docker_containers", []):
                    if container["name"] == self.container_name:
                        container["status"] = "running"
                self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        if self.coordinator.docker_manager:
            success = await self.coordinator.docker_manager.control_container(self.container_name, "stop")
            if success:
                for container in self.coordinator.data.get("docker_containers", []):
                    if container["name"] == self.container_name:
                        container["status"] = "exited"  # Docker停止后状态为exited
                self.async_write_ha_state()