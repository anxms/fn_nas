import logging
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import asyncssh
import json

from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD,
    CONF_IGNORE_DISKS, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL,
    DEFAULT_PORT, CONF_MAC, CONF_UPS_SCAN_INTERVAL, DEFAULT_UPS_SCAN_INTERVAL,
    CONF_ROOT_PASSWORD
)
from .disk_manager import DiskManager
from .system_manager import SystemManager
from .ups_manager import UPSManager
from .vm_manager import VMManager


_LOGGER = logging.getLogger(__name__)

class FlynasCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config) -> None:
        self.config = config
        self.host = config[CONF_HOST]
        self.port = config.get(CONF_PORT, DEFAULT_PORT)
        self.username = config[CONF_USERNAME]
        self.password = config[CONF_PASSWORD]
        self.root_password = config.get(CONF_ROOT_PASSWORD)  # 获取root密码
        self.mac = config.get(CONF_MAC, "")
        self.ssh = None
        self.ssh_closed = True
        self.ups_manager = UPSManager(self)
        _LOGGER.debug("初始化vm_manager")
        self.vm_manager = VMManager(self)
        self.use_sudo = False  # 添加标志，指示是否需要使用sudo
        
        # 初始化数据字典
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
        
        # 初始化各功能管理器
        self.disk_manager = DiskManager(self)
        self.system_manager = SystemManager(self)
    
    async def async_connect(self):
        if self.ssh is None or self.ssh_closed:
            try:
                # 首先尝试使用配置的用户名连接
                self.ssh = await asyncssh.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    known_hosts=None
                )
                
                # 检查当前用户是否为 root
                if await self.is_root_user():
                    _LOGGER.debug("当前用户是 root")
                    self.use_sudo = False
                    self.ssh_closed = False
                    return True
                
                # 如果不是 root 用户，尝试使用登录密码切换到 root
                _LOGGER.debug("尝试使用登录密码切换到 root 会话")
                
                # 尝试切换到 root
                result = await self.ssh.run(
                    f"echo '{self.password}' | sudo -S -i",
                    input=self.password + "\n",  # 提供登录密码
                    timeout=10
                )
                
                # 验证是否切换到 root
                whoami_result = await self.ssh.run("whoami")
                if "root" in whoami_result.stdout:
                    _LOGGER.info("成功切换到 root 会话（使用登录密码）")
                    self.use_sudo = False
                    self.ssh_closed = False
                    return True
                else:
                    # 如果登录密码无法切换到 root，尝试使用配置的 root 密码
                    if self.root_password:
                        _LOGGER.debug("尝试使用 root 密码切换到 root 会话")
                        
                        # 尝试切换到 root
                        result = await self.ssh.run(
                            f"echo '{self.root_password}' | sudo -S -i",
                            input=self.root_password + "\n",  # 提供密码
                            timeout=10
                        )
                        
                        # 验证是否切换到 root
                        whoami_result = await self.ssh.run("whoami")
                        if "root" in whoami_result.stdout:
                            _LOGGER.info("成功切换到 root 会话（使用 root 密码）")
                            self.use_sudo = False
                            self.ssh_closed = False
                            return True
                        else:
                            _LOGGER.warning("切换到 root 会话失败，将使用 sudo")
                            self.use_sudo = True
                    else:
                        _LOGGER.warning("非 root 用户且未提供 root 密码，将使用 sudo")
                        self.use_sudo = True
                
                self.ssh_closed = False
                _LOGGER.info("SSH 连接已建立到 %s", self.host)
                return True
            except Exception as e:
                self.ssh = None
                self.ssh_closed = True
                _LOGGER.error("连接失败: %s", str(e), exc_info=True)
                return False
        return True
    
    async def is_root_user(self):
        """检查当前用户是否为root"""
        try:
            result = await self.ssh.run("id -u", timeout=5)
            return result.stdout.strip() == "0"
        except Exception:
            return False
    
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
            test_command = "echo 'connection_test'"
            result = await self.ssh.run(test_command, timeout=2)
            return result.exit_status == 0 and "connection_test" in result.stdout
        except (asyncssh.Error, TimeoutError):
            return False
    
    async def run_command(self, command: str, retries=2) -> str:
        for attempt in range(retries):
            try:
                # 检查连接状态
                if not await self.is_ssh_connected():
                    # 如果连接断开，尝试重新连接
                    if not await self.async_connect():
                        # 如果重新连接失败，更新系统状态为关闭
                        if self.data and "system" in self.data:
                            self.data["system"]["status"] = "off"
                        raise UpdateFailed("SSH 连接失败")
                
                # 如果需要 sudo，在命令前添加 sudo -i
                if self.use_sudo:
                    # 使用 sudo -S 并提供密码（如果可用）
                    if self.root_password or self.password:
                        # 优先使用root密码，如果没有则使用登录密码
                        password = self.root_password if self.root_password else self.password
                        full_command = f"sudo -S {command}"
                        result = await self.ssh.run(full_command, input=password + "\n", check=True)
                    else:
                        full_command = f"sudo {command}"
                        result = await self.ssh.run(full_command, check=True)
                else:
                    result = await self.ssh.run(command, check=True)
                
                return result.stdout.strip()
            
            except asyncssh.process.ProcessError as e:
                if e.exit_status in [4, 32]:
                    return ""
                _LOGGER.error("Command failed: %s (exit %d)", command, e.exit_status)
                self.ssh = None
                self.ssh_closed = True
                if attempt == retries - 1:
                    # 更新系统状态为关闭
                    if self.data and "system" in self.data:
                        self.data["system"]["status"] = "off"
                    raise UpdateFailed(f"Command failed after {retries} attempts: {command}") from e
                
            except asyncssh.Error as e:
                _LOGGER.error("SSH connection error: %s", str(e))
                self.ssh = None
                self.ssh_closed = True
                if attempt == retries - 1:
                    # 更新系统状态为关闭
                    if self.data and "system" in self.data:
                        self.data["system"]["status"] = "off"
                    raise UpdateFailed(f"SSH error after {retries} attempts: {str(e)}") from e
                
            except Exception as e:
                self.ssh = None
                self.ssh_closed = True
                _LOGGER.error("Unexpected error: %s", str(e), exc_info=True)
                if attempt == retries - 1:
                    # 更新系统状态为关闭
                    if self.data and "system" in self.data:
                        self.data["system"]["status"] = "off"
                    raise UpdateFailed(f"Unexpected error after {retries} attempts") from e
    
    async def _async_update_data(self):
        _LOGGER.debug("Starting data update...")
        
        try:
            # 检查SSH连接状态并更新系统状态
            if await self.is_ssh_connected():
                status = "on"
            else:
                # 尝试重新连接
                if not await self.async_connect():
                    status = "off"
                else:
                    status = "on"
            
            # 获取磁盘信息
            disks = await self.disk_manager.get_disks_info()
            _LOGGER.debug("Retrieved %d disks", len(disks))
            
            # 获取系统信息（包括CPU温度、主板温度）
            system = await self.system_manager.get_system_info()
            _LOGGER.debug("System info: %s", system)

            # 获取UPS信息
            ups_info = await self.ups_manager.get_ups_info()
            _LOGGER.debug("UPS info: %s", ups_info)

            # 获取虚拟机信息
            vms = await self.vm_manager.get_vm_list()
            # 获取每个虚拟机的标题
            for vm in vms:
                vm["title"] = await self.vm_manager.get_vm_title(vm["name"])
            
            _LOGGER.debug("Retrieved %d VMs", len(vms))
            
            # 组合所有数据
            data = {
                "disks": disks,
                "system": {
                    **system,
                    "status": status  # 使用检测到的状态
                },
                "ups": ups_info, # 添加UPS信息
                "vms": vms  # 添加虚拟机数据
            }
            
            # 记录关键信息
            if "cpu_temperature" in system:
                _LOGGER.debug("CPU temperature: %s", system["cpu_temperature"])
            if "motherboard_temperature" in system:
                _LOGGER.debug("Motherboard temperature: %s", system["motherboard_temperature"])
            
            return data
        
        except Exception as e:
            _LOGGER.error("Failed to update data: %s", str(e), exc_info=True)
            # 返回空数据，但设置正确的状态
            return {
                "disks": [],
                "system": {
                    "uptime": "未知",
                    "cpu_temperature": "未知",
                    "motherboard_temperature": "未知",
                    "status": "off"  # 发生错误时设置为关闭状态
                },
                "ups": {},
                "vms": []
            }
    
    async def reboot_system(self):
        await self.system_manager.reboot_system()
    
    async def shutdown_system(self):
        await self.system_manager.shutdown_system()
        # 更新状态为关闭
        if self.data and "system" in self.data:
            self.data["system"]["status"] = "off"
        self.async_update_listeners()

class UPSDataUpdateCoordinator(DataUpdateCoordinator):
    """专门用于更新UPS数据的协调器"""
    
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
        """获取UPS数据"""
        _LOGGER.debug("Starting UPS data update...")
        try:
            return await self.ups_manager.get_ups_info()
        except Exception as e:
            _LOGGER.error("Failed to update UPS data: %s", str(e), exc_info=True)
            return {}

    async def control_vm(self, vm_name, action):
        """控制虚拟机操作"""
        try:
            _LOGGER.debug("控制虚拟机: %s, 操作: %s", vm_name, action)
            # 确保vm_manager已初始化
            if not hasattr(self, 'vm_manager'):
                _LOGGER.warning("vm_manager未初始化，正在创建实例")
                self.vm_manager = VMManager(self)
            
            # 调用vm_manager
            result = await self.vm_manager.control_vm(vm_name, action)
            _LOGGER.debug("虚拟机控制结果: %s", result)
            return result
        except Exception as e:
            _LOGGER.error("虚拟机控制失败: %s", str(e), exc_info=True)
            return False