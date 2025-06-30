import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import (
    DOMAIN, DATA_UPDATE_COORDINATOR, DEVICE_ID_NAS, CONF_ENABLE_DOCKER
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    domain_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = domain_data[DATA_UPDATE_COORDINATOR]
    enable_docker = domain_data.get(CONF_ENABLE_DOCKER, False)
    
    entities = []
    
    # 1. 添加NAS重启按钮
    entities.append(RebootButton(coordinator, config_entry.entry_id))
    
    # 2. 添加虚拟机重启按钮
    if "vms" in coordinator.data:
        for vm in coordinator.data["vms"]:
            entities.append(
                VMRebootButton(
                    coordinator, 
                    vm["name"],
                    vm.get("title", vm["name"]),
                    config_entry.entry_id
                )
            )
    
    # 3. 添加Docker容器重启按钮（如果启用了Docker功能）
    if enable_docker and "docker_containers" in coordinator.data:
        for container in coordinator.data["docker_containers"]:
            # 使用容器名称生成安全ID（替换特殊字符）
            safe_name = container["name"].replace(" ", "_").replace("/", "_").replace(".", "_")
            entities.append(
                DockerContainerRestartButton(
                    coordinator, 
                    container["name"],
                    safe_name,
                    config_entry.entry_id
                )
            )
    
    async_add_entities(entities)

class RebootButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_name = "重启"
        self._attr_unique_id = f"{entry_id}_flynas_reboot"
        self._attr_entity_category = EntityCategory.CONFIG
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

class VMRebootButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, vm_name, vm_title, entry_id):
        super().__init__(coordinator)
        self.vm_name = vm_name
        self.vm_title = vm_title
        self._attr_name = f"{vm_title} 重启"
        self._attr_unique_id = f"{entry_id}_flynas_vm_{vm_name}_reboot"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"vm_{vm_name}")},
            "name": vm_title,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }

        self.vm_manager = coordinator.vm_manager if hasattr(coordinator, 'vm_manager') else None

    async def async_press(self):
        """重启虚拟机"""
        if not self.vm_manager:
            _LOGGER.error("vm_manager不可用，无法重启虚拟机 %s", self.vm_name)
            return
            
        try:
            success = await self.vm_manager.control_vm(self.vm_name, "reboot")
            if success:
                # 更新状态为"重启中"
                for vm in self.coordinator.data["vms"]:
                    if vm["name"] == self.vm_name:
                        vm["state"] = "rebooting"
                self.async_write_ha_state()
                
                # 在下次更新时恢复实际状态
                self.coordinator.async_add_listener(self.async_write_ha_state)
        except Exception as e:
            _LOGGER.error("重启虚拟机时出错: %s", str(e), exc_info=True)

class DockerContainerRestartButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, container_name, safe_name, entry_id):
        super().__init__(coordinator)
        self.container_name = container_name
        self.safe_name = safe_name
        self._attr_name = f"{container_name} 重启"
        self._attr_unique_id = f"{entry_id}_docker_{safe_name}_restart"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"docker_{safe_name}")},
            "name": container_name,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }
        self._attr_icon = "mdi:docker"

    async def async_press(self):
        """重启Docker容器"""
        # 检查是否启用了Docker功能
        if not hasattr(self.coordinator, 'docker_manager') or self.coordinator.docker_manager is None:
            _LOGGER.error("Docker管理功能未启用，无法重启容器 %s", self.container_name)
            return
            
        try:
            # 更新状态为"重启中"
            for container in self.coordinator.data.get("docker_containers", []):
                if container["name"] == self.container_name:
                    container["status"] = "restarting"
            self.async_write_ha_state()
            
            # 执行重启命令
            success = await self.coordinator.docker_manager.control_container(self.container_name, "restart")
            
            if success:
                _LOGGER.info("Docker容器 %s 重启命令已发送", self.container_name)
                
                # 强制刷新状态（因为容器重启可能需要时间）
                self.coordinator.async_request_refresh()
            else:
                _LOGGER.error("Docker容器 %s 重启失败", self.container_name)
                # 恢复原始状态
                for container in self.coordinator.data.get("docker_containers", []):
                    if container["name"] == self.container_name:
                        container["status"] = "running"  # 假设重启失败后状态不变
                self.async_write_ha_state()
                
        except Exception as e:
            _LOGGER.error("重启Docker容器 %s 时出错: %s", self.container_name, str(e), exc_info=True)
            # 恢复原始状态
            for container in self.coordinator.data.get("docker_containers", []):
                if container["name"] == self.container_name:
                    container["status"] = "running"
            self.async_write_ha_state()
    
    @property
    def extra_state_attributes(self):
        return {
            "容器名称": self.container_name,
            "操作类型": "重启容器",
            "提示": "重启操作可能需要一些时间完成"
        }