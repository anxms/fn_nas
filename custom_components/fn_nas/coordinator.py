import logging
import re
import asyncssh
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD,
    CONF_IGNORE_DISKS, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL,
    DEFAULT_PORT, CONF_MAC, CONF_UPS_SCAN_INTERVAL, DEFAULT_UPS_SCAN_INTERVAL,
    CONF_ROOT_PASSWORD, CONF_ENABLE_DOCKER
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
        self.docker_manager = DockerManager(self) if self.enable_docker else None
        self.ssh = None
        self.ssh_closed = True
        self.ups_manager = UPSManager(self)
        self.vm_manager = VMManager(self)
        self.use_sudo = False
        
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
                
                if await self.is_root_user():
                    _LOGGER.debug("当前用户是 root")
                    self.use_sudo = False
                    self.ssh_closed = False
                    return True
                
                result = await self.ssh.run(
                    f"echo '{self.password}' | sudo -S -i",
                    input=self.password + "\n",
                    timeout=10
                )
                
                whoami_result = await self.ssh.run("whoami")
                if "root" in whoami_result.stdout:
                    _LOGGER.info("成功切换到 root 会话（使用登录密码）")
                    self.use_sudo = False
                    self.ssh_closed = False
                    return True
                else:
                    if self.root_password:
                        result = await self.ssh.run(
                            f"echo '{self.root_password}' | sudo -S -i",
                            input=self.root_password + "\n",
                            timeout=10
                        )
                        
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
        if self.ssh is None or self.ssh_closed:
            return False
        
        try:
            test_command = "echo 'connection_test'"
            result = await self.ssh.run(test_command, timeout=2)
            return result.exit_status == 0 and "connection_test" in result.stdout
        except (asyncssh.Error, TimeoutError):
            return False
    
    async def run_command(self, command: str, retries=2) -> str:
        for attempt in range(retries):
            try:
                if not await self.is_ssh_connected():
                    if not await self.async_connect():
                        if self.data and "system" in self.data:
                            self.data["system"]["status"] = "off"
                        raise UpdateFailed("SSH 连接失败")
                
                if self.use_sudo:
                    if self.root_password or self.password:
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
                    if self.data and "system" in self.data:
                        self.data["system"]["status"] = "off"
                    raise UpdateFailed(f"Command failed after {retries} attempts: {command}") from e
                
            except asyncssh.Error as e:
                _LOGGER.error("SSH connection error: %s", str(e))
                self.ssh = None
                self.ssh_closed = True
                if attempt == retries - 1:
                    if self.data and "system" in self.data:
                        self.data["system"]["status"] = "off"
                    raise UpdateFailed(f"SSH error after {retries} attempts: {str(e)}") from e
                
            except Exception as e:
                self.ssh = None
                self.ssh_closed = True
                _LOGGER.error("Unexpected error: %s", str(e), exc_info=True)
                if attempt == retries - 1:
                    if self.data and "system" in self.data:
                        self.data["system"]["status"] = "off"
                    raise UpdateFailed(f"Unexpected error after {retries} attempts") from e
    
    async def get_network_macs(self):
        try:
            output = await self.run_command("ip link show")
            macs = {}
            
            pattern = re.compile(r'^\d+: (\w+):.*\n\s+link/\w+\s+([0-9a-fA-F:]{17})', re.MULTILINE)
            matches = pattern.findall(output)
            
            for interface, mac in matches:
                if interface == "lo" or mac == "00:00:00:00:00:00":
                    continue
                macs[mac] = interface
                
            return macs
        except Exception as e:
            self.logger.error("获取MAC地址失败: %s", str(e))
            return {}
    
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
            
            disks = await self.disk_manager.get_disks_info()
            system = await self.system_manager.get_system_info()
            ups_info = await self.ups_manager.get_ups_info()
            vms = await self.vm_manager.get_vm_list()
            
            for vm in vms:
                vm["title"] = await self.vm_manager.get_vm_title(vm["name"])

            docker_containers = []
            if self.enable_docker:
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