import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
import asyncssh
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
    CONF_FAN_CONFIG_PATH,
    CONF_UPS_SCAN_INTERVAL, 
    DEFAULT_UPS_SCAN_INTERVAL,
    CONF_ROOT_PASSWORD
)

_LOGGER = logging.getLogger(__name__)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理飞牛NAS的配置流程"""
    
    VERSION = 1
    
    def __init__(self):
        super().__init__()
        self.ssh_config = None
        self.root_password = None
    
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                # 保存用户输入
                self.ssh_config = user_input
                
                # 测试SSH连接
                test_result = await self.test_connection(user_input)
                if test_result != "success":
                    errors["base"] = test_result
                else:
                    # 检查是否需要root密码
                    conn = await self.create_ssh_connection(user_input)
                    if not await self.is_root_user(conn):
                        # 非root用户，尝试使用登录密码切换到root
                        try:
                            # 尝试使用登录密码切换到root
                            result = await conn.run(
                                f"echo '{user_input[CONF_PASSWORD]}' | sudo -S whoami",
                                input=user_input[CONF_PASSWORD] + "\n",  # 提供登录密码
                                timeout=10
                            )
                            
                            # 检查输出是否为root
                            if "root" in result.stdout:
                                _LOGGER.info("登录密码可用于sudo操作，无需输入root密码")
                                # 使用登录密码作为root密码
                                user_input[CONF_ROOT_PASSWORD] = user_input[CONF_PASSWORD]
                                return self.async_create_entry(
                                    title=user_input[CONF_HOST], 
                                    data=user_input
                                )
                        except Exception as e:
                            _LOGGER.debug("登录密码无法用于sudo操作: %s", str(e))
                        
                        # 如果登录密码无法切换到root，跳转到root密码输入步骤
                        return await self.async_step_root_password()
                    
                    # 是root用户，直接创建配置项
                    return self.async_create_entry(
                        title=user_input[CONF_HOST], 
                        data=user_input
                    )
            except Exception as e:
                _LOGGER.error("Connection test failed: %s", str(e), exc_info=True)
                errors["base"] = "unknown_error"
        
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
    
    async def async_step_root_password(self, user_input=None):
        """处理root密码输入"""
        errors = {}
        if user_input is not None:
            self.root_password = user_input[CONF_ROOT_PASSWORD]
            
            # 测试使用root密码切换
            if await self.test_root_switch():
                # 成功切换到root，创建配置项
                self.ssh_config[CONF_ROOT_PASSWORD] = self.root_password
                return self.async_create_entry(
                    title=self.ssh_config[CONF_HOST], 
                    data=self.ssh_config
                )
            else:
                errors["base"] = "root_switch_failed"
        
        # 创建root密码输入表单
        return self.async_show_form(
            step_id="root_password",
            data_schema=vol.Schema({
                vol.Required(CONF_ROOT_PASSWORD): str
            }),
            description_placeholders={
                "username": self.ssh_config[CONF_USERNAME]
            },
            errors=errors
        )
    
    async def create_ssh_connection(self, config):
        """创建SSH连接"""
        host = config[CONF_HOST]
        port = config.get(CONF_PORT, DEFAULT_PORT)
        username = config[CONF_USERNAME]
        password = config[CONF_PASSWORD]
        
        return await asyncssh.connect(
            host,
            port=port,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=10
        )
    
    async def is_root_user(self, conn):
        """检查当前用户是否为root"""
        try:
            result = await conn.run("id -u", timeout=5)
            return result.stdout.strip() == "0"
        except Exception:
            return False
    
    async def test_root_switch(self):
        """测试使用root密码切换到root会话"""
        if not self.ssh_config or not self.root_password:
            return False
        
        conn = None
        try:
            # 创建SSH连接
            conn = await self.create_ssh_connection(self.ssh_config)
            
            # 尝试切换到root
            result = await conn.run(
                f"echo '{self.root_password}' | sudo -S whoami",
                input=self.root_password + "\n",  # 提供密码
                timeout=10
            )
            
            # 检查输出是否为root
            return "root" in result.stdout
        except Exception as e:
            _LOGGER.error("Root切换测试失败: %s", str(e), exc_info=True)
            return False
        finally:
            if conn and not conn.is_closed():
                conn.close()
    
    async def test_connection(self, config):
        """测试SSH连接是否成功"""
        conn = None
        try:
            # 创建SSH连接
            conn = await self.create_ssh_connection(config)
            
            # 测试一个简单命令
            result = await conn.run("echo 'connection_test'", timeout=5)
            if result.exit_status == 0 and "connection_test" in result.stdout:
                return "success"
            
            return "connection_failed"
        except asyncssh.Error as e:
            return f"SSH error: {str(e)}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"
        finally:
            # 修复这里：使用正确的属性名 is_closed
            if conn and not conn.is_closed():
                conn.close()
    
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
        
        # 添加忽略磁盘、风扇配置路径和UPS刷新间隔选项
        options = vol.Schema({
            vol.Optional(
                CONF_IGNORE_DISKS,
                default=data.get(CONF_IGNORE_DISKS, "")
            ): str,
            vol.Optional(
                CONF_FAN_CONFIG_PATH,
                default=data.get(CONF_FAN_CONFIG_PATH, "")
            ): str,
            vol.Optional(
                CONF_UPS_SCAN_INTERVAL,
                default=data.get(CONF_UPS_SCAN_INTERVAL, DEFAULT_UPS_SCAN_INTERVAL)
            ): int
        })
        
        return self.async_show_form(
            step_id="init",
            data_schema=options,
            description_placeholders={
                "config_entry": self.config_entry.title
            }
        )