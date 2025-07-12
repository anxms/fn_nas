import logging
import asyncio
import os
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

class SystemManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.logger = _LOGGER.getChild("system_manager")
        self.logger.setLevel(logging.DEBUG)
        self.debug_enabled = False  # 调试模式开关
        self.sensors_debug_path = "/config/fn_nas_debug"  # 调试文件保存路径
    
    async def get_system_info(self) -> dict:
        """获取系统信息"""
        system_info = {}
        try:
            # 获取原始运行时间（秒数）
            uptime_output = await self.coordinator.run_command("cat /proc/uptime")
            if uptime_output:
                try:
                    uptime_seconds = float(uptime_output.split()[0])
                    system_info["uptime_seconds"] = uptime_seconds
                    system_info["uptime"] = self.format_uptime(uptime_seconds)
                except (ValueError, IndexError):
                    system_info["uptime_seconds"] = 0
                    system_info["uptime"] = "未知"
            else:
                system_info["uptime_seconds"] = 0
                system_info["uptime"] = "未知"

            # 只通过内核方式获取温度
            cpu_temp = await self.get_cpu_temp_from_kernel()
            system_info["cpu_temperature"] = cpu_temp

            mobo_temp = await self.get_mobo_temp_from_kernel()
            system_info["motherboard_temperature"] = mobo_temp


            mem_info = await self.get_memory_info()
            system_info.update(mem_info)
            vol_info = await self.get_vol_usage()
            system_info["volumes"] = vol_info
            return system_info

        except Exception as e:
            self.logger.error("Error getting system info: %s", str(e))
            return {
                "uptime_seconds": 0,
                "uptime": "未知",
                "cpu_temperature": "未知",
                "motherboard_temperature": "未知",
                "memory_total": "未知",
                "memory_used": "未知",
                "memory_available": "未知",
                "volumes": {}
            }

    async def get_cpu_temp_from_kernel(self) -> str:
        # 获取CPU温度
        for i in range(5):
            for j in range(5):
                label_path = f"/sys/class/hwmon/hwmon{i}/temp{j}_label"
                label = await self.coordinator.run_command(f"cat {label_path} 2>/dev/null")
                if label and ("cpu" in label.lower() or "package" in label.lower()):
                    temp_path = f"/sys/class/hwmon/hwmon{i}/temp{j}_input"
                    temp_str = await self.coordinator.run_command(f"cat {temp_path} 2>/dev/null")
                    if temp_str and temp_str.isdigit():
                        temp = float(temp_str) / 1000.0
                        return f"{temp:.1f} °C"
        return "未知"

    async def get_mobo_temp_from_kernel(self) -> str:
        # 获取主板温度
        for i in range(5):
            for j in range(5):
                label_path = f"/sys/class/hwmon/hwmon{i}/temp{j}_label"
                label = await self.coordinator.run_command(f"cat {label_path} 2>/dev/null")
                if label and ("mobo" in label.lower() or "mb" in label.lower() or "sys" in label.lower() or "pch" in label.lower()):
                    temp_path = f"/sys/class/hwmon/hwmon{i}/temp{j}_input"
                    temp_str = await self.coordinator.run_command(f"cat {temp_path} 2>/dev/null")
                    if temp_str and temp_str.isdigit():
                        temp = float(temp_str) / 1000.0
                        return f"{temp:.1f} °C"
        return "未知"
    
    def extract_cpu_temp(self, sensors_output: str) -> str:
        """兼容旧接口，直接返回未知"""
        return "未知"

    def extract_mobo_temp(self, sensors_output: str) -> str:
        """兼容旧接口，直接返回未知"""
        return "未知"

    def format_uptime(self, seconds: float) -> str:
        """格式化运行时间为易读格式"""
        try:
            days, remainder = divmod(seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            parts = []
            if days >= 1:
                parts.append(f"{int(days)}天")
            if hours >= 1:
                parts.append(f"{int(hours)}小时")
            if minutes >= 1 or not parts:  # 如果时间很短也要显示分钟
                parts.append(f"{int(minutes)}分钟")
                
            return " ".join(parts)
        except Exception as e:
            self.logger.error("Failed to format uptime: %s", str(e))
            return "未知"
    
    async def get_memory_info(self) -> dict:
        """获取内存使用信息"""
        try:
            # 使用 free 命令获取内存信息（-b 选项以字节为单位）
            mem_output = await self.coordinator.run_command("free -b")
            if not mem_output:
                return {}
            
            # 解析输出
            lines = mem_output.splitlines()
            if len(lines) < 2:
                return {}
                
            # 第二行是内存信息（Mem行）
            mem_line = lines[1].split()
            if len(mem_line) < 7:
                return {}
                
            return {
                "memory_total": int(mem_line[1]),
                "memory_used": int(mem_line[2]),
                "memory_available": int(mem_line[6])
            }
            
        except Exception as e:
            self.logger.error("获取内存信息失败: %s", str(e))
            return {}
    
    async def get_vol_usage(self) -> dict:
        """获取 /vol* 开头的存储卷使用信息"""
        try:
            # 优先使用字节单位
            df_output = await self.coordinator.run_command("df -B 1 /vol* 2>/dev/null")
            if df_output:
                return self.parse_df_bytes(df_output)
            
            df_output = await self.coordinator.run_command("df -h /vol*")
            if df_output:
                return self.parse_df_human_readable(df_output)
                
            return {}
        except Exception as e:
            self.logger.error("获取存储卷信息失败: %s", str(e))
            return {}
    
    def parse_df_bytes(self, df_output: str) -> dict:
        volumes = {}
        for line in df_output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
                
            mount_point = parts[-1]
            # 只处理 /vol 开头的挂载点
            if not mount_point.startswith("/vol"):
                continue
                
            try:
                size_bytes = int(parts[1])
                used_bytes = int(parts[2])
                avail_bytes = int(parts[3])
                use_percent = parts[4]
                
                def bytes_to_human(b):
                    for unit in ['', 'K', 'M', 'G', 'T']:
                        if abs(b) < 1024.0:
                            return f"{b:.1f}{unit}"
                        b /= 1024.0
                    return f"{b:.1f}P"
                
                volumes[mount_point] = {
                    "filesystem": parts[0],
                    "size": bytes_to_human(size_bytes),
                    "used": bytes_to_human(used_bytes),
                    "available": bytes_to_human(avail_bytes),
                    "use_percent": use_percent
                }
            except (ValueError, IndexError) as e:
                self.logger.debug("解析存储卷行失败: %s - %s", line, str(e))
                continue
                    
        return volumes
    
    def parse_df_human_readable(self, df_output: str) -> dict:
        volumes = {}
        for line in df_output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
                
            mount_point = parts[-1]
            if not mount_point.startswith("/vol"):
                continue
                
            try:
                size = parts[1]
                used = parts[2]
                avail = parts[3]
                use_percent = parts[4]
                
                volumes[mount_point] = {
                    "filesystem": parts[0],
                    "size": size,
                    "used": used,
                    "available": avail,
                    "use_percent": use_percent
                }
            except (ValueError, IndexError) as e:
                self.logger.debug("解析存储卷行失败: %s - %s", line, str(e))
                continue
                
        return volumes              
        
    async def reboot_system(self):
        """重启系统"""
        self.logger.info("Initiating system reboot...")
        try:
            await self.coordinator.run_command("sudo reboot")
            self.logger.info("Reboot command sent")
            
            if "system" in self.coordinator.data:
                self.coordinator.data["system"]["status"] = "rebooting"
                self.coordinator.async_update_listeners()
        except Exception as e:
            self.logger.error("Failed to reboot system: %s", str(e))
            raise
    
    async def shutdown_system(self):
        """关闭系统"""
        self.logger.info("Initiating system shutdown...")
        try:
            await self.coordinator.run_command("sudo shutdown -h now")
            self.logger.info("Shutdown command sent")
            
            if "system" in self.coordinator.data:
                self.coordinator.data["system"]["status"] = "off"
                self.coordinator.async_update_listeners()
        except Exception as e:
            self.logger.error("Failed to shutdown system: %s", str(e))
            raise