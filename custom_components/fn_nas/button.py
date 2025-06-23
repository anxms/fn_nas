import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, DATA_UPDATE_COORDINATOR, DEVICE_ID_NAS

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    domain_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = domain_data[DATA_UPDATE_COORDINATOR]
    
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
                    config_entry.entry_id  # 传递entry_id用于生成唯一ID
                )
            )
    
    async_add_entities(entities)

class RebootButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_name = "重启"
        self._attr_unique_id = f"{entry_id}_flynas_reboot"  # 使用entry_id确保唯一性
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
        self._attr_unique_id = f"{entry_id}_flynas_vm_{vm_name}_reboot"  # 使用entry_id确保唯一性
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