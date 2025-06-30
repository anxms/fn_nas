import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, DATA_UPDATE_COORDINATOR, PLATFORMS, CONF_ENABLE_DOCKER  # 导入新增常量
from .coordinator import FlynasCoordinator, UPSDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    config = {**entry.data, **entry.options}
    
    coordinator = FlynasCoordinator(hass, config)
    await coordinator.async_config_entry_first_refresh()

    _LOGGER.debug("协调器类型: %s", type(coordinator).__name__)
    _LOGGER.debug("协调器是否有control_vm方法: %s", hasattr(coordinator, 'control_vm'))
    _LOGGER.debug("协调器是否有vm_manager属性: %s", hasattr(coordinator, 'vm_manager'))
    
    # 检查是否启用Docker，并初始化Docker管理器（如果有）
    enable_docker = config.get(CONF_ENABLE_DOCKER, False)
    if enable_docker:
        # 导入Docker管理器并初始化
        from .docker_manager import DockerManager
        coordinator.docker_manager = DockerManager(coordinator)
        _LOGGER.debug("已启用Docker容器监控")
    else:
        coordinator.docker_manager = None
        _LOGGER.debug("未启用Docker容器监控")
    
    ups_coordinator = UPSDataUpdateCoordinator(hass, config, coordinator)
    await ups_coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_UPDATE_COORDINATOR: coordinator,
        "ups_coordinator": ups_coordinator,
        CONF_ENABLE_DOCKER: enable_docker  # 存储启用状态
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
        
        # 关闭主协调器的SSH连接
        await coordinator.async_disconnect()
        # 关闭UPS协调器
        await ups_coordinator.async_shutdown()
        
        # 从DOMAIN中移除该entry的数据
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok