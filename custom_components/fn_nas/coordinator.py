# coordinator.py (文档9)
import logging
import asyncio
import asyncssh
import re
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
        
        # 确保data始终有初始值
        self.data = self.get_default_data()
        
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
        self._last_command_time = 0
        self._command_count = 0
    
    def get_default_data(self):
        """返回默认的数据结构"""
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
    
    async def async_connect(self):
        """建立并保持持久SSH连接"""
        if self.ssh is not None and not self.ssh_closed:
            try:
                # 测试连接是否仍然活跃
                await self.ssh.run("echo 'connection_test'", timeout=1)
                return True
            except (asyncssh.Error, TimeoutError):
                _LOGGER.debug("现有连接失效，准备重建")
                await self.async_disconnect()
        
        try:
            self.ssh = await asyncssh.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                known_hosts=None,
                connect_timeout=5
            )
            
            self.ssh_closed = False
            _LOGGER.info("已建立持久SSH连接到 %s", self.host)
            
            # 检查权限状态
            if await self.is_root_user():
                _LOGGER.debug("当前用户是 root")
                self.use_sudo = False
            else:
                # 尝试切换到root会话
                if await self.try_switch_to_root():
                    self.use_sudo = False
            
            return True
        
        except Exception as e:
            self.ssh = None
            self.ssh_closed = True
            _LOGGER.debug("连接失败: %s", str(e))
            return False
    
    async def try_switch_to_root(self):
        """尝试切换到root会话"""
        try:
            if self.root_password:
                result = await self.ssh.run(
                    f"echo '{self.root_password}' | sudo -S -i",
                    input=self.root_password + "\n",
                    timeout=5
                )
                whoami = await self.ssh.run("whoami")
                if "root" in whoami.stdout:
                    _LOGGER.info("成功切换到 root 会话（使用 root 密码）")
                    return True
                
            result = await self.ssh.run(
                f"echo '{self.password}' | sudo -S -i",
                input=self.password + "\n",
                timeout=5
            )
            whoami = await self.ssh.run("whoami")
            if "root" in whoami.stdout:
                _LOGGER.info("成功切换到 root 会话（使用登录密码）")
                return True
                
            self.use_sudo = True
            return False
        except Exception:
            self.use_sudo = True
            return False
    
    async def is_root_user(self):
        try:
            result = await self.ssh.run("id -u", timeout=3)
            return result.stdout.strip() == "0"
        except Exception:
            return False
    
    async def async_disconnect(self):
        """断开SSH连接"""
        if self.ssh is not None and not self.ssh_closed:
            try:
                self.ssh.close()
                self.ssh_closed = True
                _LOGGER.debug("已关闭SSH连接")
            except Exception as e:
                _LOGGER.debug("关闭SSH连接时出错: %s", str(e))
            finally:
                self.ssh = None
    
    async def run_command(self, command: str, retries=2) -> str:
        """执行SSH命令，使用持久连接"""
        current_time = asyncio.get_event_loop().time()
        
        # 连接冷却机制：避免短时间内频繁创建新连接
        if current_time - self._last_command_time < 1.0 and self._command_count > 5:
            await asyncio.sleep(0.5)
        
        self._last_command_time = current_time
        self._command_count += 1
        
        # 系统离线时直接返回空字符串
        if not self._system_online:
            return ""
            
        try:
            # 确保连接有效
            if not await self.async_connect():
                return ""
                
            # 使用sudo执行命令
            if self.use_sudo:
                if self.root_password or self.password:
                    password = self.root_password if self.root_password else self.password
                    full_command = f"sudo -S {command}"
                    result = await self.ssh.run(full_command, input=password + "\n", timeout=10)
                else:
                    full_command = f"sudo {command}"
                    result = await self.ssh.run(full_command, timeout=10)
            else:
                result = await self.ssh.run(command, timeout=10)
                
            return result.stdout.strip()
        
        except (asyncssh.Error, TimeoutError) as e:
            _LOGGER.debug("命令执行失败: %s, 错误: %s", command, str(e))
            # 标记连接失效
            self.ssh_closed = True
            return ""
        except Exception as e:
            _LOGGER.debug("执行命令时出现意外错误: %s", str(e))
            self.ssh_closed = True
            return ""
    
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
    
    async def _async_update_data(self):
        """数据更新入口，优化命令执行频率"""
        _LOGGER.debug("开始数据更新...")
        is_online = await self.ping_system()
        self._system_online = is_online
        
        if not is_online:
            _LOGGER.debug("系统离线，跳过数据更新")
            # 启动后台监控任务
            if not self._ping_task or self._ping_task.done():
                self._ping_task = asyncio.create_task(self._monitor_system_status())
            await self.async_disconnect()
            return self.get_default_data()
        
        # 系统在线处理
        try:
            # 确保连接有效
            if not await self.async_connect():
                return self.get_default_data()
                
            # 获取系统状态信息
            status = "on"
            # 并行获取磁盘、UPS和系统信息
            system_task = asyncio.create_task(self.system_manager.get_system_info())
            disks_task = asyncio.create_task(self.disk_manager.get_disks_info())
            ups_task = asyncio.create_task(self.ups_manager.get_ups_info())
            vms_task = asyncio.create_task(self.vm_manager.get_vm_list())
            
            # 等待并行任务完成
            system, disks, ups_info, vms = await asyncio.gather(
                system_task, disks_task, ups_task, vms_task
            )
            
            # 为每个虚拟机获取标题
            for vm in vms:
                vm["title"] = await self.vm_manager.get_vm_title(vm["name"])
            
            # 获取Docker容器信息
            docker_containers = []
            if self.enable_docker:
                docker_containers = await self.docker_manager.get_containers()
            
            data = {
                "disks": disks,
                "system": {**system, "status": status},
                "ups": ups_info,
                "vms": vms,
                "docker_containers": docker_containers
            }
            
            return data
        
        except Exception as e:
            _LOGGER.debug("数据更新失败: %s", str(e))
            self._system_online = False
            if not self._ping_task or self._ping_task.done():
                self._ping_task = asyncio.create_task(self._monitor_system_status())
                
            return self.get_default_data()
    
    def get_default_data(self):
        """获取默认数据（离线状态）"""
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
    
    async def reboot_system(self):
        await self.system_manager.reboot_system()
    
    async def shutdown_system(self):
        await self.system_manager.shutdown_system()
        # 更新状态，但使用安全的方式
        if self.data and isinstance(self.data, dict) and "system" in self.data:
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