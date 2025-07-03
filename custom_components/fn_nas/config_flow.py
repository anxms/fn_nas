import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
import asyncssh
import re
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
    CONF_ROOT_PASSWORD,
    CONF_ENABLE_DOCKER,
    CONF_MAX_CONNECTIONS,
    DEFAULT_MAX_CONNECTIONS,
    CONF_CACHE_TIMEOUT,
    DEFAULT_CACHE_TIMEOUT
)

_LOGGER = logging.getLogger(__name__)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理飞牛NAS的配置流程"""
    
    VERSION = 1
    
    def __init__(self):
        super().__init__()
        self.ssh_config = None
    
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
                    if await self.is_root_user(conn):
                        # 是root用户，直接使用
                        self.ssh_config[CONF_ROOT_PASSWORD] = self.ssh_config[CONF_PASSWORD]
                        return await self.async_step_select_mac()
                    elif await self.test_sudo_with_password(conn, user_input[CONF_PASSWORD]):
                        # 非root用户但可使用密码sudo
                        self.ssh_config[CONF_ROOT_PASSWORD] = self.ssh_config[CONF_PASSWORD]
                        return await self.async_step_select_mac()
                    else:
                        # 无法获取root权限
                        errors["base"] = "sudo_permission_required"
            except Exception as e:
                _LOGGER.error("Connection test failed: %s", str(e), exc_info=True)
                errors["base"] = "unknown_error"
        
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(
                CONF_SCAN_INTERVAL, 
                default=DEFAULT_SCAN_INTERVAL
            ): int,
            # 添加启用Docker的选项
            vol.Optional(CONF_ENABLE_DOCKER, default=False): bool,
            # 新增：最大连接数
            vol.Optional(
                CONF_MAX_CONNECTIONS, 
                default=DEFAULT_MAX_CONNECTIONS
            ): int,
            # 新增：缓存超时时间（分钟）
            vol.Optional(
                CONF_CACHE_TIMEOUT, 
                default=DEFAULT_CACHE_TIMEOUT
            ): int
        })
        
        return self.async_show_form(
            step_id="user", 
            data_schema=schema, 
            errors=errors
        )
    
    async def async_step_select_mac(self, user_input=None):
        """在添加集成时选择MAC地址"""
        errors = {}
        mac_options = {}

        try:
            conn = await self.create_ssh_connection(self.ssh_config)
            result = await conn.run("ip link show", timeout=5)
            mac_options = self.parse_mac_addresses(result.stdout)
        except Exception as e:
            errors["base"] = f"获取网卡信息失败: {str(e)}"
            _LOGGER.error("获取网卡信息失败: %s", str(e), exc_info=True)

        if not mac_options:
            errors["base"] = "未找到网卡MAC地址"

        if user_input is not None:
            selected_mac = user_input.get(CONF_MAC)
            if selected_mac:
                # 将CONF_ENABLE_DOCKER从ssh_config复制到最终配置
                enable_docker = self.ssh_config.get(CONF_ENABLE_DOCKER, False)
                self.ssh_config[CONF_MAC] = selected_mac
                # 确保将CONF_ENABLE_DOCKER也存入配置项
                self.ssh_config[CONF_ENABLE_DOCKER] = enable_docker
                # 添加连接池和缓存配置
                self.ssh_config[CONF_MAX_CONNECTIONS] = self.ssh_config.get(CONF_MAX_CONNECTIONS, DEFAULT_MAX_CONNECTIONS)
                self.ssh_config[CONF_CACHE_TIMEOUT] = self.ssh_config.get(CONF_CACHE_TIMEOUT, DEFAULT_CACHE_TIMEOUT)
                return self.async_create_entry(
                    title=self.ssh_config[CONF_HOST],
                    data=self.ssh_config
                )
            else:
                errors["base"] = "请选择一个MAC地址"

        schema = vol.Schema({
            vol.Required(CONF_MAC): vol.In(mac_options)
        })

        return self.async_show_form(
            step_id="select_mac",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "host": self.ssh_config[CONF_HOST]
            }
        )

    def parse_mac_addresses(self, output: str) -> dict:
        """从ip link命令输出中解析MAC地址"""
        mac_options = {}
        pattern = re.compile(r'^\d+: (\w+):.*\n\s+link/\w+\s+([0-9a-fA-F:]{17})', re.MULTILINE)
        matches = pattern.findall(output)
        
        for interface, mac in matches:
            if interface == "lo" or mac == "00:00:00:00:00:00":
                continue
            mac_options[mac] = f"{interface} - {mac}"
        
        return mac_options
    
    async def create_ssh_connection(self, config):
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
        try:
            result = await conn.run("id -u", timeout=5)
            return result.stdout.strip() == "0"
        except Exception:
            return False
            
    async def test_sudo_with_password(self, conn, password):
        try:
            result = await conn.run(
                f"echo '{password}' | sudo -S whoami",
                input=password + "\n",
                timeout=10
            )
            return "root" in result.stdout
        except Exception:
            return False
    
    async def test_connection(self, config):
        conn = None
        try:
            conn = await self.create_ssh_connection(config)
            result = await conn.run("echo 'connection_test'", timeout=5)
            if result.exit_status == 0 and "connection_test" in result.stdout:
                return "success"
            return "connection_failed"
        except asyncssh.Error as e:
            return f"SSH error: {str(e)}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"
        finally:
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
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        
        data = self.config_entry.options or self.config_entry.data
        
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
            ): int,
            # 在选项流程中也添加启用Docker的选项
            vol.Optional(
                CONF_ENABLE_DOCKER,
                default=data.get(CONF_ENABLE_DOCKER, False)
            ): bool,
            # 新增：最大连接数
            vol.Optional(
                CONF_MAX_CONNECTIONS,
                default=data.get(CONF_MAX_CONNECTIONS, DEFAULT_MAX_CONNECTIONS)
            ): int,
            # 新增：缓存超时时间（分钟）
            vol.Optional(
                CONF_CACHE_TIMEOUT,
                default=data.get(CONF_CACHE_TIMEOUT, DEFAULT_CACHE_TIMEOUT)
            ): int
        })
        
        return self.async_show_form(
            step_id="init",
            data_schema=options,
            description_placeholders={
                "config_entry": self.config_entry.title
            }
        )