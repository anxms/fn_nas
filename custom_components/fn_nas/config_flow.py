import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import (
    CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD,
    CONF_SCAN_INTERVAL, CONF_MAC
)
from .const import (
    DOMAIN, 
    DEFAULT_PORT, 
    DEFAULT_SCAN_INTERVAL,
    CONF_IGNORE_DISKS,
    CONF_FAN_CONFIG_PATH
)

_LOGGER = logging.getLogger(__name__)  # 现在logging模块已导入

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理飞牛NAS的配置流程"""
    
    VERSION = 1
    
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                # 测试连接
                # 在实际实现中，这里会尝试连接设备验证凭证
                return self.async_create_entry(
                    title=user_input[CONF_HOST], 
                    data=user_input
                )
            except Exception as e:
                _LOGGER.error("Connection test failed: %s", str(e))
                errors["base"] = "cannot_connect"
        
        # 添加MAC地址字段
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_MAC, default=""): str,  # MAC地址用于唤醒
            vol.Optional(
                CONF_SCAN_INTERVAL, 
                default=DEFAULT_SCAN_INTERVAL
            ): int
        })
        
        return self.async_show_form(
            step_id="user", 
            data_schema=schema, 
            errors=errors
        )
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)

class OptionsFlowHandler(config_entries.OptionsFlow):
    """处理飞牛NAS的选项流程"""
    
    def __init__(self, config_entry):
        self.config_entry = config_entry
    
    async def async_step_init(self, user_input=None):
        """管理选项"""
        if user_input is not None:
            # 保存选项
            return self.async_create_entry(title="", data=user_input)
        
        # 获取当前配置值
        data = self.config_entry.options or self.config_entry.data
        
        # 添加忽略磁盘和风扇配置路径选项
        options = vol.Schema({
            vol.Optional(
                CONF_IGNORE_DISKS,
                default=data.get(CONF_IGNORE_DISKS, "")
            ): str,
            vol.Optional(
                CONF_FAN_CONFIG_PATH,
                default=data.get(CONF_FAN_CONFIG_PATH, "")
            ): str
        })
        
        return self.async_show_form(
            step_id="init",
            data_schema=options,
            description_placeholders={
                "config_entry": self.config_entry.title
            }
        )