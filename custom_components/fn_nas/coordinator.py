import logging
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import asyncssh
import json

from .disk_manager import DiskManager
from .system_manager import SystemManager

from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD,
    CONF_IGNORE_DISKS, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL,
    DEFAULT_PORT, CONF_MAC
)
from .disk_manager import DiskManager
from .system_manager import SystemManager


_LOGGER = logging.getLogger(__name__)

class FlynasCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config) -> None:
        self.config = config
        self.host = config[CONF_HOST]
        self.port = config.get(CONF_PORT, DEFAULT_PORT)
        self.username = config[CONF_USERNAME]
        self.password = config[CONF_PASSWORD]
        self.mac = config.get(CONF_MAC, "")
        self.ssh = None
        self.ssh_closed = True  # 添加连接状态标志
        
        scan_interval = config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        update_interval = timedelta(seconds=scan_interval)
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval
        )
        
        # 初始化各功能管理器
        self.disk_manager = DiskManager(self)
        self.system_manager = SystemManager(self)
    
    async def async_connect(self):
        if self.ssh is None or self.ssh_closed:
            try:
                self.ssh = await asyncssh.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    known_hosts=None
                )
                self.ssh_closed = False
                _LOGGER.info("SSH connection established to %s", self.host)
            except Exception as e:
                self.ssh = None
                self.ssh_closed = True
                _LOGGER.error("Failed to connect: %s", str(e))
                raise
    
    async def async_disconnect(self):
        if self.ssh is not None and not self.ssh_closed:
            try:
                self.ssh.close()
                self.ssh_closed = True
                _LOGGER.info("SSH connection closed")
            except Exception as e:
                _LOGGER.error("Error closing SSH connection: %s", str(e))
            finally:
                self.ssh = None
    
    async def is_ssh_connected(self) -> bool:
        """检查 SSH 连接是否有效"""
        if self.ssh is None or self.ssh_closed:
            return False
        
        try:
            # 发送一个简单的命令来测试连接是否仍然有效
            result = await self.ssh.run("echo 'connection_test'", timeout=2, check=True)
            return result.exit_status == 0
        except (asyncssh.Error, TimeoutError):
            return False
    
    async def run_command(self, command: str, retries=2) -> str:
        for attempt in range(retries):
            try:
                # 使用更可靠的方式检查连接状态
                if self.ssh is None or self.ssh_closed or not await self.is_ssh_connected():
                    _LOGGER.debug("SSH connection not active, reconnecting...")
                    await self.async_connect()
                
                result = await self.ssh.run(command, check=True)
                return result.stdout.strip()
            
            except asyncssh.process.ProcessError as e:
                if e.exit_status in [4, 32]:
                    return ""
                _LOGGER.error("Command failed: %s (exit %d)", command, e.exit_status)
                self.ssh = None
                self.ssh_closed = True
                if attempt == retries - 1:
                    raise UpdateFailed(f"Command failed after {retries} attempts: {command}") from e
                
            except asyncssh.Error as e:
                _LOGGER.error("SSH connection error: %s", str(e))
                self.ssh = None
                self.ssh_closed = True
                if attempt == retries - 1:
                    raise UpdateFailed(f"SSH error after {retries} attempts: {str(e)}") from e
                
            except Exception as e:
                self.ssh = None
                self.ssh_closed = True
                _LOGGER.error("Unexpected error: %s", str(e), exc_info=True)
                if attempt == retries - 1:
                    raise UpdateFailed(f"Unexpected error after {retries} attempts") from e
    
    async def _async_update_data(self):
        # 添加更多调试信息
        _LOGGER.debug("Starting data update...")
        
        try:
            # 获取磁盘信息
            disks = await self.disk_manager.get_disks_info()
            _LOGGER.debug("Retrieved %d disks", len(disks))
            
            # 获取系统信息（包括CPU温度、主板温度）
            system = await self.system_manager.get_system_info()
            _LOGGER.debug("System info: %s", system)
            
            
            # 组合所有数据（添加系统状态）
            data = {
                "disks": disks,
                "system": {
                    **system,
                    "status": "on"  # 添加系统状态
                },
            }
            
            # 记录关键信息
            if "cpu_temperature" in system:
                _LOGGER.debug("CPU temperature: %s", system["cpu_temperature"])
            if "motherboard_temperature" in system:
                _LOGGER.debug("Motherboard temperature: %s", system["motherboard_temperature"])
            if "fans" in system:
                for fan in system["fans"]:
                    _LOGGER.debug("Fan %s: %d RPM", fan["id"], fan["speed"])
            
            return data
        
        except Exception as e:
            _LOGGER.error("Failed to update data: %s", str(e), exc_info=True)
            # 返回空数据，但保持结构完整
            return {
                "disks": [],
                "system": {
                    "uptime": "未知",
                    "cpu_temperature": "未知",
                    "motherboard_temperature": "未知",
                    "status": "unknown"
                },
            }
    
    async def reboot_system(self):
        await self.system_manager.reboot_system()
    
    async def shutdown_system(self):
        await self.system_manager.shutdown_system()
    