import logging
import asyncio
import os
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

class SystemManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.logger = _LOGGER.getChild("system_manager")
        # 根据Home Assistant的日志级别动态设置
        self.logger.setLevel(logging.DEBUG if _LOGGER.isEnabledFor(logging.DEBUG) else logging.INFO)
        self.debug_enabled = _LOGGER.isEnabledFor(logging.DEBUG)  # 基于HA调试模式
        self.sensors_debug_path = "/config/fn_nas_debug"
        
        # 温度传感器缓存
        self.cpu_temp_cache = {
            "hwmon_id": None,
            "temp_id": None,
            "driver_type": None,
            "label": None
        }
        self.mobo_temp_cache = {
            "hwmon_id": None,
            "temp_id": None,
            "label": None
        }

    def _debug_log(self, message: str):
        """只在调试模式下输出详细日志"""
        if self.debug_enabled:
            self.logger.debug(message)

    def _info_log(self, message: str):
        """重要信息日志"""
        self.logger.info(message)

    def _warning_log(self, message: str):
        """警告日志"""
        self.logger.warning(message)

    def _error_log(self, message: str):
        """错误日志"""
        self.logger.error(message)

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

            # 一次性获取CPU和主板温度
            temps = await self.get_temperatures_from_sensors()
            system_info["cpu_temperature"] = temps["cpu"]
            system_info["motherboard_temperature"] = temps["motherboard"]

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

    async def get_temperatures_from_sensors(self) -> dict:
        """一次性获取CPU和主板温度"""
        try:
            command = "sensors"
            self._debug_log(f"执行sensors命令获取温度: {command}")
            
            sensors_output = await self.coordinator.run_command(command)
            if self.debug_enabled:
                self._debug_log(f"sensors命令输出长度: {len(sensors_output) if sensors_output else 0}")
            
            if not sensors_output:
                self._warning_log("sensors命令无输出")
                return {"cpu": "未知", "motherboard": "未知"}
            
            # 同时解析CPU和主板温度
            cpu_temp = self.extract_cpu_temp_from_sensors(sensors_output)
            mobo_temp = self.extract_mobo_temp_from_sensors(sensors_output)
            
            # 记录获取结果
            if cpu_temp != "未知":
                self._info_log(f"通过sensors获取CPU温度成功: {cpu_temp}")
            else:
                self._warning_log("sensors命令未找到CPU温度")
                
            if mobo_temp != "未知":
                self._info_log(f"通过sensors获取主板温度成功: {mobo_temp}")
            else:
                self._warning_log("sensors命令未找到主板温度")
            
            return {"cpu": cpu_temp, "motherboard": mobo_temp}
            
        except Exception as e:
            self._error_log(f"使用sensors命令获取温度失败: {e}")
            return {"cpu": "未知", "motherboard": "未知"}

    async def get_cpu_temp_from_kernel(self) -> str:
        """获取CPU温度 - 向后兼容"""
        temps = await self.get_temperatures_from_sensors()
        return temps["cpu"]

    async def get_mobo_temp_from_kernel(self) -> str:
        """获取主板温度 - 向后兼容"""
        temps = await self.get_temperatures_from_sensors()
        return temps["motherboard"]

    async def get_cpu_temp_from_sensors(self) -> str:
        """使用sensors命令获取CPU温度 - 向后兼容"""
        temps = await self.get_temperatures_from_sensors()
        return temps["cpu"]

    async def get_mobo_temp_from_sensors(self) -> str:
        """使用sensors命令获取主板温度 - 向后兼容"""
        temps = await self.get_temperatures_from_sensors()
        return temps["motherboard"]

    def extract_cpu_temp_from_sensors(self, sensors_output: str) -> str:
        """从sensors输出中提取CPU温度"""
        try:
            lines = sensors_output.split('\n')
            self._debug_log(f"解析sensors输出，共{len(lines)}行")
            
            for i, line in enumerate(lines):
                line_lower = line.lower().strip()
                if self.debug_enabled:
                    self._debug_log(f"第{i+1}行: {line_lower}")
                
                # AMD CPU温度关键词
                if any(keyword in line_lower for keyword in [
                    "tctl", "tdie", "k10temp"
                ]):
                    self._debug_log(f"找到AMD CPU温度行: {line}")
                    if '+' in line and '°c' in line_lower:
                        try:
                            temp_match = line.split('+')[1].split('°')[0].strip()
                            temp = float(temp_match)
                            if 0 < temp < 150:
                                self._info_log(f"从sensors提取AMD CPU温度: {temp:.1f}°C")
                                return f"{temp:.1f} °C"
                        except (ValueError, IndexError) as e:
                            self._debug_log(f"解析AMD温度失败: {e}")
                            continue
                
                # Intel CPU温度关键词
                if any(keyword in line_lower for keyword in [
                    "package id", "core 0", "coretemp"
                ]) and not any(exclude in line_lower for exclude in ["fan"]):
                    self._debug_log(f"找到Intel CPU温度行: {line}")
                    if '+' in line and '°c' in line_lower:
                        try:
                            temp_match = line.split('+')[1].split('°')[0].strip()
                            temp = float(temp_match)
                            if 0 < temp < 150:
                                self._info_log(f"从sensors提取Intel CPU温度: {temp:.1f}°C")
                                return f"{temp:.1f} °C"
                        except (ValueError, IndexError) as e:
                            self._debug_log(f"解析Intel温度失败: {e}")
                            continue
                
                # 通用CPU温度模式
                if ('cpu' in line_lower or 'processor' in line_lower) and '+' in line and '°c' in line_lower:
                    self._debug_log(f"找到通用CPU温度行: {line}")
                    try:
                        temp_match = line.split('+')[1].split('°')[0].strip()
                        temp = float(temp_match)
                        if 0 < temp < 150:
                            self._info_log(f"从sensors提取通用CPU温度: {temp:.1f}°C")
                            return f"{temp:.1f} °C"
                    except (ValueError, IndexError) as e:
                        self._debug_log(f"解析通用CPU温度失败: {e}")
                        continue
            
            self._warning_log("未在sensors输出中找到CPU温度")
            return "未知"
            
        except Exception as e:
            self._error_log(f"解析sensors CPU温度输出失败: {e}")
            return "未知"

    def extract_mobo_temp_from_sensors(self, sensors_output: str) -> str:
        """从sensors输出中提取主板温度"""
        try:
            lines = sensors_output.split('\n')
            self._debug_log(f"解析主板温度，共{len(lines)}行")
            
            for i, line in enumerate(lines):
                line_lower = line.lower().strip()
                
                # 主板温度关键词
                if any(keyword in line_lower for keyword in [
                    "motherboard", "mobo", "mb", "system", "chipset", 
                    "ambient", "temp1:", "temp2:", "temp3:", "systin"
                ]) and not any(cpu_keyword in line_lower for cpu_keyword in [
                    "cpu", "core", "package", "processor", "tctl", "tdie"
                ]) and not any(exclude in line_lower for exclude in ["fan", "rpm"]):
                    
                    self._debug_log(f"找到可能的主板温度行: {line}")
                    
                    if '+' in line and '°c' in line_lower:
                        try:
                            temp_match = line.split('+')[1].split('°')[0].strip()
                            temp = float(temp_match)
                            # 主板温度通常在15-70度之间
                            if 15 <= temp <= 70:
                                self._info_log(f"从sensors提取主板温度: {temp:.1f}°C")
                                return f"{temp:.1f} °C"
                            else:
                                self._debug_log(f"主板温度值超出合理范围: {temp:.1f}°C")
                        except (ValueError, IndexError) as e:
                            self._debug_log(f"解析主板温度失败: {e}")
                            continue
            
            self._warning_log("未在sensors输出中找到主板温度")
            return "未知"
            
        except Exception as e:
            self._error_log(f"解析sensors主板温度输出失败: {e}")
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
            self._error_log(f"获取内存信息失败: {str(e)}")
            return {}
    
    async def get_vol_usage(self) -> dict:
        """获取 /vol* 开头的存储卷使用信息，避免唤醒休眠磁盘"""
        try:
            # 首先检查哪些卷是活跃的，避免访问休眠磁盘
            active_vols = await self.check_active_volumes()
            
            if active_vols:
                # 只查询活跃的卷，避免使用通配符可能唤醒所有磁盘
                vol_list = " ".join(active_vols)
                df_output = await self.coordinator.run_command(f"df -B 1 {vol_list} 2>/dev/null")
                if df_output:
                    return self.parse_df_bytes(df_output)
                
                df_output = await self.coordinator.run_command(f"df -h {vol_list} 2>/dev/null")
                if df_output:
                    return self.parse_df_human_readable(df_output)
            
            # 如果没有活跃卷或者上述方法失败，使用缓存或者返回空
            return {}
            
        except Exception as e:
            self.logger.error("获取存储卷信息失败: %s", str(e))
            return {}
    
    async def check_active_volumes(self) -> list:
        """检查当前活跃的存储卷，避免唤醒休眠磁盘"""
        try:
            # 获取所有挂载点，这个操作不会访问磁盘内容
            mount_output = await self.coordinator.run_command("mount | grep '/vol'")
            active_vols = []
            
            for line in mount_output.splitlines():
                if '/vol' in line:
                    # 提取挂载点
                    parts = line.split()
                    for part in parts:
                        if part.startswith('/vol'):
                            # 检查这个卷对应的磁盘是否活跃
                            if await self.is_volume_disk_active(part):
                                active_vols.append(part)
                            break
            
            self._debug_log(f"检测到活跃存储卷: {active_vols}")
            return active_vols
            
        except Exception as e:
            self._debug_log(f"检查活跃存储卷失败: {e}")
            return []
    
    async def is_volume_disk_active(self, mount_point: str) -> bool:
        """检查存储卷对应的磁盘是否活跃"""
        try:
            # 获取挂载点对应的设备
            device_output = await self.coordinator.run_command(f"findmnt -n -o SOURCE {mount_point} 2>/dev/null")
            if not device_output:
                return False
            
            device = device_output.strip()
            # 提取设备名（去掉分区号）
            import re
            device_match = re.search(r'/dev/([a-zA-Z]+)', device)
            if device_match:
                device_name = device_match.group(1)
                
                # 检查设备的I/O统计，不直接访问磁盘
                stat_path = f"/sys/block/{device_name}/stat"
                stat_output = await self.coordinator.run_command(f"cat {stat_path} 2>/dev/null")
                
                if stat_output:
                    stats = stat_output.split()
                    if len(stats) >= 9:
                        in_flight = int(stats[8])  # 当前进行中的I/O
                        return in_flight > 0  # 有I/O活动认为是活跃的
            
            return False
            
        except Exception as e:
            self._debug_log(f"检查卷磁盘活跃状态失败 {mount_point}: {e}")
            return False
    
    async def reboot_system(self):
        """重启系统"""
        self._info_log("Initiating system reboot...")
        try:
            await self.coordinator.run_command("sudo reboot")
            self._info_log("Reboot command sent")
            
            if "system" in self.coordinator.data:
                self.coordinator.data["system"]["status"] = "rebooting"
                self.coordinator.async_update_listeners()
        except Exception as e:
            self._error_log(f"Failed to reboot system: {str(e)}")
            raise
    
    async def shutdown_system(self):
        """关闭系统"""
        self._info_log("Initiating system shutdown...")
        try:
            await self.coordinator.run_command("sudo shutdown -h now")
            self._info_log("Shutdown command sent")
            
            if "system" in self.coordinator.data:
                self.coordinator.data["system"]["status"] = "off"
                self.coordinator.async_update_listeners()
        except Exception as e:
            self._error_log(f"Failed to shutdown system: {str(e)}")
            raise