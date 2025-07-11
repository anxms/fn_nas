import logging
import asyncio
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
    def __init__(self, hass: HomeAssistant, config, config_entry) -> None:
        self.config = config
        self.config_entry = config_entry
        self.hass = hass
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
        self._system_online = False
        self._ping_task = None
        self._retry_interval = 30  # 系统离线时的检测间隔（秒）
    
    async def async_connect(self):
        if self.ssh is None or self.ssh_closed:
            try:
                self.ssh = await asyncssh.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    known_hosts=None,
                    connect_timeout=5  # 缩短连接超时时间
                )
                
                if await self.is_root_user():
                    _LOGGER.debug("当前用户是 root")
                    self.use_sudo = False
                    self.ssh_closed = False
                    return True
                
                result = await self.ssh.run(
                    f"echo '{self.password}' | sudo -S -i",
                    input=self.password + "\n",
                    timeout=5
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
                            timeout=5
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
                _LOGGER.debug("连接失败: %s", str(e))
                return False
        return True
    
    async def is_root_user(self):
        try:
            result = await self.ssh.run("id -u", timeout=3)
            return result.stdout.strip() == "0"
        except Exception:
            return False
    
    async def async_disconnect(self):
        if self.ssh is not None and not self.ssh_closed:
            try:
                self.ssh.close()
                self.ssh_closed = True
                _LOGGER.debug("SSH connection closed")
            except Exception as e:
                _LOGGER.debug("Error closing SSH connection: %s", str(e))
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
    
    async def ping_system(self) -> bool:
        """轻量级系统状态检测"""
        # 对于本地主机直接返回True
        if self.host in ['localhost', '127.0.0.1']:
            return True
            
        try:
            # 使用异步ping检测
            proc = await asyncio.create_subprocess_exec(
                'ping', '-c', '1', '-W', '1', self.host,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False
    
    async def run_command(self, command: str, retries=2) -> str:
        # 系统离线时直接返回空字符串，避免抛出异常
        if not self._system_online:
            return ""
            
        for attempt in range(retries):
            try:
                if not await self.is_ssh_connected():
                    if not await self.async_connect():
                        if self.data and "system" in self.data:
                            self.data["system"]["status"] = "off"
                        return ""
                
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
                _LOGGER.debug("Command failed: %s (exit %d)", command, e.exit_status)
                self.ssh = None
                self.ssh_closed = True
                if attempt == retries - 1:
                    return ""
                
            except asyncssh.Error as e:
                _LOGGER.debug("SSH connection error: %s", str(e))
                self.ssh = None
                self.ssh_closed = True
                if attempt == retries - 1:
                    return ""
                
            except Exception as e:
                self.ssh = None
                self.ssh_closed = True
                _LOGGER.debug("Unexpected error: %s", str(e))
                if attempt == retries - 1:
                    return ""
        return ""
    
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
            self.logger.debug("获取MAC地址失败: %s", str(e))
            return {}
    
    async def _monitor_system_status(self):
        """系统离线时轮询检测状态"""
        self.logger.debug("启动系统状态监控，每%d秒检测一次", self._retry_interval)
        while True:
            await asyncio.sleep(self._retry_interval)
            
            if await self.ping_system():
                self.logger.info("检测到系统已开机，触发重新加载")
                # 触发集成重新加载
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )
                break
    
    async def _async_update_data(self):
        _LOGGER.debug("Starting data update...")
        
        # 先进行轻量级系统状态检测
        is_online = await self.ping_system()
        self._system_online = is_online
        
        # 系统离线处理
        if not is_online:
            _LOGGER.debug("系统离线，跳过数据更新")
            self.data["system"]["status"] = "off"
            
            # 启动监控任务（如果尚未启动）
            if not self._ping_task or self._ping_task.done():
                self._ping_task = asyncio.create_task(self._monitor_system_status())
            
            # 关闭SSH连接
            await self.async_disconnect()
            
            return {
                "disks": [],
                "system": {
                    "uptime": "未知",
                    "cpu_temperature": "未知",
                    "motherboard_temperature": "未知",
                    "status": "off"
                },
                "ups": {},
                "vms": [],
                "docker_containers": []
            }
        
        # 系统在线处理
        try:
            # 确保SSH连接
            if not await self.async_connect():
                self.data["system"]["status"] = "off"
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
            _LOGGER.debug("数据更新失败: %s", str(e))
            # 检查错误类型，如果是连接问题，标记为离线
            self._system_online = False
            if not self._ping_task or self._ping_task.done():
                self._ping_task = asyncio.create_task(self._monitor_system_status())
                
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
        # 如果主协调器检测到系统离线，跳过UPS更新
        if not self.main_coordinator._system_online:
            return {}
        
        try:
            return await self.ups_manager.get_ups_info()
        except Exception as e:
            _LOGGER.debug("UPS数据更新失败: %s", str(e))
            return {}

    async def control_vm(self, vm_name, action):
        try:
            if not hasattr(self, 'vm_manager'):
                self.vm_manager = VMManager(self)
            
            result = await self.vm_manager.control_vm(vm_name, action)
            return result
        except Exception as e:
            _LOGGER.debug("虚拟机控制失败: %s", str(e))
            return False