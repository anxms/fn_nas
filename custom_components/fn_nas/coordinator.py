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
        # SSH连接池管理
        self.ssh_pool = []
        self.ssh_pool_size = 3  # 连接池大小
        self.ssh_pool_lock = asyncio.Lock()
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
    
        # 添加日志方法
        self.debug_enabled = _LOGGER.isEnabledFor(logging.DEBUG)
    
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
    
    def _debug_log(self, message: str):
        """只在调试模式下输出详细日志"""
        if self.debug_enabled:
            _LOGGER.debug(message)

    def _info_log(self, message: str):
        """重要信息日志"""
        _LOGGER.info(message)

    def _warning_log(self, message: str):
        """警告日志"""
        _LOGGER.warning(message)

    def _error_log(self, message: str):
        """错误日志"""
        _LOGGER.error(message)

    async def get_ssh_connection(self):
        """从连接池获取可用的SSH连接"""
        async with self.ssh_pool_lock:
            # 检查现有连接
            for i, (ssh, in_use) in enumerate(self.ssh_pool):
                if not in_use:
                    try:
                        # 测试连接是否活跃
                        await asyncio.wait_for(ssh.run("echo 'test'", timeout=1), timeout=2)
                        self.ssh_pool[i] = (ssh, True)  # 标记为使用中
                        self._debug_log(f"复用连接池中的连接 {i}")
                        return ssh, i
                    except Exception:
                        # 连接失效，移除
                        try:
                            ssh.close()
                        except:
                            pass
                        self.ssh_pool.pop(i)
                        break
            
            # 如果连接池未满，创建新连接
            if len(self.ssh_pool) < self.ssh_pool_size:
                try:
                    ssh = await asyncssh.connect(
                        self.host,
                        port=self.port,
                        username=self.username,
                        password=self.password,
                        known_hosts=None,
                        connect_timeout=5
                    )
                    
                    # 检查并设置权限状态
                    await self._setup_connection_permissions(ssh)
                    
                    connection_id = len(self.ssh_pool)
                    self.ssh_pool.append((ssh, True))
                    self._debug_log(f"创建新的SSH连接 {connection_id}")
                    return ssh, connection_id
                except Exception as e:
                    self._debug_log(f"创建SSH连接失败: {e}")
                    raise
            
            # 连接池满且所有连接都在使用中，等待可用连接
            self._debug_log("所有连接都在使用中，等待可用连接...")
            for _ in range(50):  # 最多等待5秒
                await asyncio.sleep(0.1)
                for i, (ssh, in_use) in enumerate(self.ssh_pool):
                    if not in_use:
                        try:
                            await asyncio.wait_for(ssh.run("echo 'test'", timeout=1), timeout=2)
                            self.ssh_pool[i] = (ssh, True)
                            self._debug_log(f"等待后获得连接 {i}")
                            return ssh, i
                        except Exception:
                            try:
                                ssh.close()
                            except:
                                pass
                            self.ssh_pool.pop(i)
                            break
            
            raise Exception("无法获取SSH连接")

    async def _setup_connection_permissions(self, ssh):
        """为新连接设置权限状态"""
        try:
            # 检查是否为root用户
            result = await ssh.run("id -u", timeout=3)
            if result.stdout.strip() == "0":
                self._debug_log("当前用户是 root")
                self.use_sudo = False
                return
            
            # 尝试切换到root会话
            if self.root_password:
                try:
                    await ssh.run(
                        f"echo '{self.root_password}' | sudo -S -i",
                        input=self.root_password + "\n",
                        timeout=5
                    )
                    whoami = await ssh.run("whoami")
                    if "root" in whoami.stdout:
                        self._info_log("成功切换到 root 会话（使用 root 密码）")
                        self.use_sudo = False
                        return
                except Exception:
                    pass
            
            # 尝试使用登录密码sudo
            try:
                await ssh.run(
                    f"echo '{self.password}' | sudo -S -i",
                    input=self.password + "\n",
                    timeout=5
                )
                whoami = await ssh.run("whoami")
                if "root" in whoami.stdout:
                    self._info_log("成功切换到 root 会话（使用登录密码）")
                    self.use_sudo = False
                    return
            except Exception:
                pass
                
            # 设置为使用sudo模式
            self.use_sudo = True
            self._debug_log("设置为使用sudo模式")
            
        except Exception as e:
            self._debug_log(f"设置连接权限失败: {e}")
            self.use_sudo = True

    async def release_ssh_connection(self, connection_id):
        """释放SSH连接回连接池"""
        async with self.ssh_pool_lock:
            if 0 <= connection_id < len(self.ssh_pool):
                ssh, _ = self.ssh_pool[connection_id]
                self.ssh_pool[connection_id] = (ssh, False)  # 标记为可用
                self._debug_log(f"释放SSH连接 {connection_id}")
    
    async def close_all_ssh_connections(self):
        """关闭所有SSH连接"""
        async with self.ssh_pool_lock:
            for ssh, _ in self.ssh_pool:
                try:
                    ssh.close()
                except:
                    pass
            self.ssh_pool.clear()
            self._debug_log("已关闭所有SSH连接")
    
    async def async_connect(self):
        """建立并保持持久SSH连接 - 兼容旧代码"""
        try:
            ssh, connection_id = await self.get_ssh_connection()
            await self.release_ssh_connection(connection_id)
            return True
        except Exception:
            return False
    
    async def async_disconnect(self):
        """断开SSH连接 - 兼容旧代码"""
        await self.close_all_ssh_connections()
    
    async def run_command(self, command: str, retries=2) -> str:
        """执行SSH命令，使用连接池"""
        # 系统离线时直接返回空字符串
        if not self._system_online:
            return ""
        
        ssh = None
        connection_id = None
        
        try:
            # 从连接池获取连接
            ssh, connection_id = await self.get_ssh_connection()
            
            # 构建完整命令
            if self.use_sudo:
                if self.root_password or self.password:
                    password = self.root_password if self.root_password else self.password
                    full_command = f"sudo -S {command}"
                    result = await ssh.run(full_command, input=password + "\n", timeout=10)
                else:
                    full_command = f"sudo {command}"
                    result = await ssh.run(full_command, timeout=10)
            else:
                result = await ssh.run(command, timeout=10)
            
            return result.stdout.strip()
        
        except Exception as e:
            self._debug_log(f"命令执行失败: {command}, 错误: {str(e)}")
            return ""
        
        finally:
            # 释放连接回连接池
            if connection_id is not None:
                await self.release_ssh_connection(connection_id)
    
    async def run_command_direct(self, command: str) -> str:
        """直接执行命令，获取独立连接 - 用于并发任务"""
        if not self._system_online:
            return ""
        
        ssh = None
        connection_id = None
        
        try:
            ssh, connection_id = await self.get_ssh_connection()
            
            if self.use_sudo:
                if self.root_password or self.password:
                    password = self.root_password if self.root_password else self.password
                    full_command = f"sudo -S {command}"
                    result = await ssh.run(full_command, input=password + "\n", timeout=10)
                else:
                    full_command = f"sudo {command}"
                    result = await ssh.run(full_command, timeout=10)
            else:
                result = await ssh.run(command, timeout=10)
            
            return result.stdout.strip()
        
        except Exception as e:
            self._debug_log(f"直接命令执行失败: {command}, 错误: {str(e)}")
            return ""
        
        finally:
            if connection_id is not None:
                await self.release_ssh_connection(connection_id)
    
    async def ping_system(self) -> bool:
        """轻量级系统状态检测"""
        # 对于本地主机直接返回True
        if self.host in ['localhost', '127.0.0.1']:
            return True
            
        try:
            # 使用异步ping检测，减少超时时间
            proc = await asyncio.create_subprocess_exec(
                'ping', '-c', '1', '-W', '1', self.host,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=2)  # 总超时时间2秒
            return proc.returncode == 0
        except Exception:
            return False
    
    async def _monitor_system_status(self):
        """系统离线时轮询检测状态"""
        self._debug_log(f"启动系统状态监控，每{self._retry_interval}秒检测一次")
        
        # 使用指数退避策略，避免频繁检测
        check_interval = self._retry_interval
        max_interval = 300  # 最大5分钟检测一次
        
        while True:
            await asyncio.sleep(check_interval)
            
            if await self.ping_system():
                self._info_log("检测到系统已开机，触发重新加载")
                # 触发集成重新加载
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )
                break
            else:
                # 系统仍然离线，增加检测间隔（指数退避）
                check_interval = min(check_interval * 1.5, max_interval)
                self._debug_log(f"系统仍离线，下次检测间隔: {check_interval}秒")
    
    async def _async_update_data(self):
        """数据更新入口，优化命令执行频率"""
        self._debug_log("开始数据更新...")
        is_online = await self.ping_system()
        self._system_online = is_online
        
        if not is_online:
            self._debug_log("系统离线，跳过数据更新")
            # 启动后台监控任务
            if not self._ping_task or self._ping_task.done():
                self._ping_task = asyncio.create_task(self._monitor_system_status())
            await self.close_all_ssh_connections()
            return self.get_default_data()
        
        # 系统在线处理
        try:
            # 预热连接池并确保权限设置正确
            await self.async_connect()
            
            # 获取系统状态信息
            status = "on"
            
            # 串行获取信息以确保稳定性
            self._debug_log("开始获取系统信息...")
            system = await self.system_manager.get_system_info()
            self._debug_log("系统信息获取完成")
            
            self._debug_log("开始获取磁盘信息...")
            disks = await self.disk_manager.get_disks_info()
            self._debug_log(f"磁盘信息获取完成，数量: {len(disks)}")
            
            self._debug_log("开始获取UPS信息...")
            ups_info = await self.ups_manager.get_ups_info()
            self._debug_log("UPS信息获取完成")
            
            self._debug_log("开始获取虚拟机信息...")
            vms = await self.vm_manager.get_vm_list()
            self._debug_log(f"虚拟机信息获取完成，数量: {len(vms)}")
            
            # 为每个虚拟机获取标题
            for vm in vms:
                try:
                    vm["title"] = await self.vm_manager.get_vm_title(vm["name"])
                except Exception as e:
                    self._debug_log(f"获取VM标题失败 {vm['name']}: {e}")
                    vm["title"] = vm["name"]
            
            # 获取Docker容器信息
            docker_containers = []
            if self.enable_docker and self.docker_manager:
                self._debug_log("开始获取Docker信息...")
                try:
                    docker_containers = await self.docker_manager.get_containers()
                    self._debug_log(f"Docker信息获取完成，数量: {len(docker_containers)}")
                except Exception as e:
                    self._debug_log(f"Docker信息获取失败: {e}")
            
            data = {
                "disks": disks,
                "system": {**system, "status": status},
                "ups": ups_info,
                "vms": vms,
                "docker_containers": docker_containers
            }
            
            self._debug_log(f"数据更新完成: disks={len(disks)}, vms={len(vms)}, containers={len(docker_containers)}")
            return data
        
        except Exception as e:
            self._error_log(f"数据更新失败: {str(e)}")
            self._system_online = False
            if not self._ping_task or self._ping_task.done():
                self._ping_task = asyncio.create_task(self._monitor_system_status())
                
            return self.get_default_data()

    async def shutdown_system(self):
        """关闭系统 - 委托给SystemManager"""
        return await self.system_manager.shutdown_system()
    
    async def reboot_system(self):
        """重启系统 - 委托给SystemManager"""
        return await self.system_manager.reboot_system()

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
            result = await self.main_coordinator.vm_manager.control_vm(vm_name, action)
            return result
        except Exception as e:
            _LOGGER.debug("虚拟机控制失败: %s", str(e))
            return False