import logging
import re
import asyncssh
import asyncio
import time
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD,
    CONF_IGNORE_DISKS, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL,
    DEFAULT_PORT, CONF_MAC, CONF_UPS_SCAN_INTERVAL, DEFAULT_UPS_SCAN_INTERVAL,
    CONF_ROOT_PASSWORD, CONF_ENABLE_DOCKER, CONF_MAX_CONNECTIONS, DEFAULT_MAX_CONNECTIONS,
    CONF_CACHE_TIMEOUT, DEFAULT_CACHE_TIMEOUT
)
from .disk_manager import DiskManager
from .system_manager import SystemManager
from .ups_manager import UPSManager
from .vm_manager import VMManager
from .docker_manager import DockerManager

_LOGGER = logging.getLogger(__name__)

class FlynasCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config) -> None:
        self.config = config
        self.host = config[CONF_HOST]
        self.port = config.get(CONF_PORT, DEFAULT_PORT)
        self.username = config[CONF_USERNAME]
        self.password = config[CONF_PASSWORD]
        self.root_password = config.get(CONF_ROOT_PASSWORD)
        self.mac = config.get(CONF_MAC, "")
        self.enable_docker = config.get(CONF_ENABLE_DOCKER, False)
        self.max_connections = config.get(CONF_MAX_CONNECTIONS, DEFAULT_MAX_CONNECTIONS)
        self.cache_timeout = config.get(CONF_CACHE_TIMEOUT, DEFAULT_CACHE_TIMEOUT) * 60
        self.docker_manager = DockerManager(self) if self.enable_docker else None
        self.ssh_pool = []  # SSH连接池
        self.active_commands = 0  # 当前活动命令数
        self.ssh_closed = True  # 初始状态为关闭
        self.use_sudo = False  # 初始化use_sudo属性
        
        self.data = {
            "disks": [],
            "system": {
                "uptime": "未知",
                "cpu_temperature": "未知",
                "motherboard_temperature": "未知",
                "status": "off"
            },
            "ups": {},
            "vms": []
        }
        
        scan_interval = config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        update_interval = timedelta(seconds=scan_interval)
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval
        )
        
        self.disk_manager = DiskManager(self)
        self.system_manager = SystemManager(self)
        self.ups_manager = UPSManager(self) 
        self.vm_manager = VMManager(self)
    
    async def get_ssh_connection(self):
        """从连接池获取或创建SSH连接"""
        # 如果连接池中有可用连接且没有超过最大活动命令数
        while len(self.ssh_pool) > 0 and self.active_commands < self.max_connections:
            conn = self.ssh_pool.pop()
            if await self.is_connection_alive(conn):
                self.active_commands += 1
                return conn
            else:
                await self.close_connection(conn)
        
        # 如果没有可用连接，创建新连接
        if self.active_commands < self.max_connections:
            try:
                conn = await asyncssh.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    known_hosts=None,
                    connect_timeout=10
                )
                self.active_commands += 1
                self.ssh_closed = False
                
                # 确定是否需要sudo权限
                await self.determine_sudo_setting(conn)
                
                return conn
            except Exception as e:
                _LOGGER.error("创建SSH连接失败: %s", str(e), exc_info=True)
                raise UpdateFailed(f"SSH连接失败: {str(e)}")
        else:
            await asyncio.sleep(0.1)
            return await self.get_ssh_connection()

    async def determine_sudo_setting(self, conn):
        """确定是否需要使用sudo权限"""
        try:
            # 检查当前用户是否是root
            result = await conn.run("id -u", timeout=5)
            if result.stdout.strip() == "0":
                _LOGGER.debug("当前用户是root，不需要sudo")
                self.use_sudo = False
                return
        except Exception as e:
            _LOGGER.warning("检查用户ID失败: %s", str(e))
        
        # 检查是否可以使用密码sudo
        try:
            result = await conn.run(
                f"echo '{self.password}' | sudo -S whoami",
                input=self.password + "\n",
                timeout=10
            )
            if "root" in result.stdout:
                _LOGGER.info("可以使用用户密码sudo")
                self.use_sudo = True
                return
        except Exception as e:
            _LOGGER.debug("无法使用用户密码sudo: %s", str(e))
        
        # 如果有root密码，尝试使用root密码sudo
        if self.root_password:
            try:
                result = await conn.run(
                    f"echo '{self.root_password}' | sudo -S whoami",
                    input=self.root_password + "\n",
                    timeout=10
                )
                if "root" in result.stdout:
                    _LOGGER.info("可以使用root密码sudo")
                    self.use_sudo = True
                    return
            except Exception as e:
                _LOGGER.debug("无法使用root密码sudo: %s", str(e))
        
        _LOGGER.warning("无法获取root权限，将使用普通用户执行命令")
        self.use_sudo = False

    async def release_ssh_connection(self, conn):
        """释放连接回连接池"""
        self.active_commands -= 1
        if conn and not conn.is_closed():
            if len(self.ssh_pool) < self.max_connections:
                self.ssh_pool.append(conn)
            else:
                await self.close_connection(conn)
        else:
            # 如果连接已经关闭，直接丢弃
            pass

    async def close_connection(self, conn):
        """关闭SSH连接"""
        try:
            if conn and not conn.is_closed():
                conn.close()
        except Exception as e:
            _LOGGER.debug("关闭SSH连接时出错: %s", str(e))

    async def is_connection_alive(self, conn) -> bool:
        """检查连接是否存活"""
        try:
            # 发送一个简单的命令测试连接
            result = await conn.run("echo 'connection_test'", timeout=2)
            return result.exit_status == 0 and "connection_test" in result.stdout
        except (asyncssh.Error, TimeoutError, ConnectionResetError):
            return False

    async def run_command(self, command: str, retries=2) -> str:
        """使用连接池执行命令"""
        conn = None
        try:
            conn = await self.get_ssh_connection()
            
            # 根据sudo设置执行命令
            if self.use_sudo:
                password = self.root_password if self.root_password else self.password
                if password:
                    full_command = f"sudo -S {command}"
                    result = await conn.run(full_command, input=password + "\n", check=True)
                else:
                    full_command = f"sudo {command}"
                    result = await conn.run(full_command, check=True)
            else:
                result = await conn.run(command, check=True)
            
            return result.stdout.strip()
        except asyncssh.process.ProcessError as e:
            if e.exit_status in [4, 32]:
                return ""
            _LOGGER.error("Command failed: %s (exit %d)", command, e.exit_status)
            # 连接可能已损坏，关闭它
            await self.close_connection(conn)
            conn = None
            if retries > 0:
                return await self.run_command(command, retries-1)
            else:
                raise UpdateFailed(f"Command failed: {command}") from e
        except asyncssh.Error as e:
            _LOGGER.error("SSH连接错误: %s", str(e))
            await self.close_connection(conn)
            conn = None
            if retries > 0:
                return await self.run_command(command, retries-1)
            else:
                raise UpdateFailed(f"SSH错误: {str(e)}") from e
        except Exception as e:
            _LOGGER.error("意外错误: %s", str(e), exc_info=True)
            await self.close_connection(conn)
            conn = None
            if retries > 0:
                return await self.run_command(command, retries-1)
            else:
                raise UpdateFailed(f"意外错误: {str(e)}") from e
        finally:
            if conn:
                await self.release_ssh_connection(conn)
    
    async def async_connect(self):
        """建立SSH连接（使用连接池）"""
        # 连接池已处理连接，此方法现在主要用于初始化
        return True
    
    async def is_ssh_connected(self) -> bool:
        """检查是否有活动的SSH连接"""
        return len(self.ssh_pool) > 0 or self.active_commands > 0
    
    async def async_disconnect(self):
        """关闭所有SSH连接"""
        # 关闭连接池中的所有连接
        for conn in self.ssh_pool:
            await self.close_connection(conn)
        self.ssh_pool = []
        self.active_commands = 0
        self.ssh_closed = True
        self.use_sudo = False  # 重置sudo设置
    
    async def _async_update_data(self):
        _LOGGER.debug("Starting data update...")
        
        try:
            if await self.is_ssh_connected():
                status = "on"
            else:
                if not await self.async_connect():
                    status = "off"
                else:
                    status = "on"
            
            # 使用已初始化的管理器获取数据
            disks = await self.disk_manager.get_disks_info()
            system = await self.system_manager.get_system_info()
            ups_info = await self.ups_manager.get_ups_info()
            vms = await self.vm_manager.get_vm_list()
            
            # 获取虚拟机标题
            for vm in vms:
                vm["title"] = await self.vm_manager.get_vm_title(vm["name"])

            # 获取Docker容器信息（如果启用）
            docker_containers = []
            if self.enable_docker and hasattr(self, 'docker_manager') and self.docker_manager:
                docker_containers = await self.docker_manager.get_containers()
            
            data = {
                "disks": disks,
                "system": {
                    **system,
                    "status": status
                },
                "ups": ups_info,
                "vms": vms,
                "docker_containers": docker_containers
            }
            
            return data
        
        except Exception as e:
            _LOGGER.error("Failed to update data: %s", str(e), exc_info=True)
            return {
                "disks": [],
                "system": {
                    "uptime": "未知",
                    "cpu_temperature": "未知",
                    "motherboard_temperature": "未知",
                    "status": "off"
                },
                "ups": {},
                "vms": []
            }
    
    async def reboot_system(self):
        await self.system_manager.reboot_system()
    
    async def shutdown_system(self):
        await self.system_manager.shutdown_system()
        if self.data and "system" in self.data:
            self.data["system"]["status"] = "off"
        self.async_update_listeners()

class UPSDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config, main_coordinator):
        self.config = config
        self.main_coordinator = main_coordinator
        
        ups_scan_interval = config.get(CONF_UPS_SCAN_INTERVAL, DEFAULT_UPS_SCAN_INTERVAL)
        update_interval = timedelta(seconds=ups_scan_interval)
        
        super().__init__(
            hass,
            _LOGGER,
            name="UPS Data",
            update_interval=update_interval
        )
        
        self.ups_manager = UPSManager(main_coordinator)
    
    async def _async_update_data(self):
        try:
            return await self.ups_manager.get_ups_info()
        except Exception as e:
            _LOGGER.error("Failed to update UPS data: %s", str(e), exc_info=True)
            return {}

    async def control_vm(self, vm_name, action):
        try:
            if not hasattr(self, 'vm_manager'):
                self.vm_manager = VMManager(self)
            
            result = await self.vm_manager.control_vm(vm_name, action)
            return result
        except Exception as e:
            _LOGGER.error("虚拟机控制失败: %s", str(e), exc_info=True)
            return False