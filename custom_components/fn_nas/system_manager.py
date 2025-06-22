import re
import logging
import asyncio
import json
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
        """获取系统信息（修复版）"""
        system_info = {}
        try:
            # 获取原始运行时间（秒数）
            uptime_output = await self.coordinator.run_command("cat /proc/uptime")
            if uptime_output:
                try:
                    # 保存原始秒数
                    uptime_seconds = float(uptime_output.split()[0])
                    system_info["uptime_seconds"] = uptime_seconds
                    # 保存格式化字符串
                    system_info["uptime"] = self.format_uptime(uptime_seconds)
                except (ValueError, IndexError):
                    system_info["uptime_seconds"] = 0
                    system_info["uptime"] = "未知"
            else:
                system_info["uptime_seconds"] = 0
                system_info["uptime"] = "未知"
            
            # 获取 sensors 命令输出（使用JSON格式）
            sensors_output = await self.coordinator.run_command(
                "sensors -j 2>/dev/null || sensors 2>/dev/null || echo 'No sensor data'"
            )
            
            # 保存传感器数据以便调试
            self.save_sensor_data_for_debug(sensors_output)
            self.logger.debug("Sensors output: %s", sensors_output[:500] + "..." if len(sensors_output) > 500 else sensors_output)
            
            # 提取 CPU 温度（改进算法）
            cpu_temp = self.extract_cpu_temp(sensors_output)
            system_info["cpu_temperature"] = cpu_temp
            
            # 提取主板温度（改进算法）
            mobo_temp = self.extract_mobo_temp(sensors_output)
            system_info["motherboard_temperature"] = mobo_temp
            
            # 尝试备用方法获取CPU温度
            if cpu_temp == "未知":
                backup_cpu_temp = await self.get_cpu_temp_fallback()
                if backup_cpu_temp:
                    system_info["cpu_temperature"] = backup_cpu_temp
            
            return system_info
            
        except Exception as e:
            self.logger.error("Error getting system info: %s", str(e))
            return {
                "uptime_seconds": 0,
                "uptime": "未知",
                "cpu_temperature": "未知",
                "motherboard_temperature": "未知"
            }
    
    def save_sensor_data_for_debug(self, sensors_output: str):
        """保存传感器数据以便调试"""
        if not self.debug_enabled:
            return
            
        try:
            # 创建调试目录
            if not os.path.exists(self.sensors_debug_path):
                os.makedirs(self.sensors_debug_path)
            
            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(self.sensors_debug_path, f"sensors_{timestamp}.log")
            
            # 写入文件
            with open(filename, "w") as f:
                f.write(sensors_output)
            
            self.logger.info("Saved sensors output to %s for debugging", filename)
        except Exception as e:
            self.logger.error("Failed to save sensor data: %s", str(e))
    
    async def get_cpu_temp_fallback(self) -> str:
        """备用方法获取CPU温度"""
        self.logger.info("Trying fallback methods to get CPU temperature")
        
        # 方法1: 从/sys/class/thermal读取
        try:
            for i in range(5):  # 检查前5个可能的传感器
                path = f"/sys/class/thermal/thermal_zone{i}/temp"
                output = await self.coordinator.run_command(f"cat {path} 2>/dev/null")
                if output and output.isdigit():
                    temp = float(output) / 1000.0
                    self.logger.info("Found CPU temperature via thermal zone: %.1f°C", temp)
                    return f"{temp:.1f} °C"
        except Exception:
            pass
        
        # 方法2: 从hwmon设备读取
        try:
            for i in range(5):  # 检查前5个可能的hwmon设备
                for j in range(5):  # 检查每个设备的前5个温度传感器
                    path = f"/sys/class/hwmon/hwmon{i}/temp{j}_input"
                    output = await self.coordinator.run_command(f"cat {path} 2>/dev/null")
                    if output and output.isdigit():
                        temp = float(output) / 1000.0
                        self.logger.info("Found CPU temperature via hwmon: %.1f°C", temp)
                        return f"{temp:.1f} °C"
        except Exception:
            pass
        
        # 方法3: 使用psutil库（如果可用）
        try:
            output = await self.coordinator.run_command("python3 -c 'import psutil; print(psutil.sensors_temperatures().get(\"coretemp\")[0].current)' 2>/dev/null")
            if output and output.replace('.', '', 1).isdigit():
                temp = float(output)
                self.logger.info("Found CPU temperature via psutil: %.1f°C", temp)
                return f"{temp:.1f} °C"
        except Exception:
            pass
        
        self.logger.warning("All fallback methods failed to get CPU temperature")
        return ""
    
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
    
    def extract_cpu_temp(self, sensors_output: str) -> str:
        """从 sensors 输出中提取 CPU 温度（优化版）"""
        # 首先尝试解析JSON格式
        if sensors_output.strip().startswith('{'):
            try:
                data = json.loads(sensors_output)
                self.logger.debug("JSON sensors data: %s", json.dumps(data, indent=2))
                
                # 查找包含CPU相关键名的温度值
                candidates = []
                for key, values in data.items():
                    # 检查是否是CPU相关的传感器
                    if any(kw in key.lower() for kw in ["core", "cpu", "package", "tccd", "k10temp", "physical"]):
                        for subkey, temp_value in values.items():
                            # 检查是否是温度输入值
                            if any(kw in subkey.lower() for kw in ["temp", "input"]) and not "crit" in subkey.lower():
                                try:
                                    if isinstance(temp_value, (int, float)):
                                        candidates.append(temp_value)
                                        self.logger.debug("Found CPU temp candidate in JSON: %s/%s = %.1f°C", key, subkey, temp_value)
                                except Exception as e:
                                    self.logger.debug("JSON value error: %s", str(e))
                
                # 如果有候选值，取平均值
                if candidates:
                    avg_temp = sum(candidates) / len(candidates)
                    return f"{avg_temp:.1f} °C"
            except Exception as e:
                self.logger.warning("Failed to parse sensors JSON: %s", str(e))
        
        # 如果JSON解析失败，使用正则表达式
        temp_values = []
        patterns = [
            r'Package id 0:\s*\+?(\d+\.?\d*)°C',
            r'Core\s*\d+:\s*\+?(\d+\.?\d*)°C',
            r'CPU Temperature:\s*\+?(\d+\.?\d*)°C',
            r'cpu_thermal:\s*\+?(\d+\.?\d*)°C',
            r'Tdie:\s*\+?(\d+\.?\d*)°C',
            r'Tctl:\s*\+?(\d+\.?\d*)°C',
            r'PECI Agent \d:\s*\+?(\d+\.?\d*)°C',
            r'CPUTIN:\s*\+?(\d+\.?\d*)°C',
            r'Composite:\s*\+?(\d+\.?\d*)°C',
            r'CPU\s+Temp:\s*\+?(\d+\.?\d*)°C',
            r'k10temp-pci\S*:\s*\+?(\d+\.?\d*)°C',  # AMD CPU
            r'temp\d+:\s*\+?(\d+\.?\d*)°C',  # 通用温度传感器
            r'Processor\s+Temp:\s*\+?(\d+\.?\d*)°C',
            r'CPU\s*:\s*\+?(\d+\.?\d*)°C',
            r'Physical id 0:\s*\+?(\d+\.?\d*)°C'
        ]
        
        found_any = False
        for pattern in patterns:
            matches = re.finditer(pattern, sensors_output, re.IGNORECASE)
            for match in matches:
                try:
                    temp = float(match.group(1))
                    temp_values.append(temp)
                    found_any = True
                    self.logger.debug("Found CPU temperature with pattern: %s: %.1f°C", pattern, temp)
                except (ValueError, IndexError) as e:
                    self.logger.debug("Pattern match error: %s", str(e))
                    continue
        
        # 如果有找到温度值，取平均值
        if temp_values:
            avg_temp = sum(temp_values) / len(temp_values)
            return f"{avg_temp:.1f} °C"
        
        # 如果所有模式都失败，尝试手动扫描所有温度值
        fallback_candidates = []
        for line in sensors_output.splitlines():
            if '°C' in line:
                # 跳过明显非CPU的行
                if any(kw in line.lower() for kw in ["fan", "vin", "volt", "+3.3", "+5", "+12", "vdd", "power", "crit", "max"]):
                    continue
                    
                # 查找温度值
                match = re.search(r'(\d+\.?\d*)\s*°C', line)
                if match:
                    try:
                        temp = float(match.group(1))
                        # 合理温度范围检查
                        if 0 < temp < 120:
                            fallback_candidates.append(temp)
                            self.logger.debug("Fallback candidate: %s -> %.1f°C", line.strip(), temp)
                    except ValueError:
                        continue
        
        # 如果有候选值，取平均值
        if fallback_candidates:
            avg_temp = sum(fallback_candidates) / len(fallback_candidates)
            self.logger.warning("Using fallback CPU temperature detection")
            return f"{avg_temp:.1f} °C"
        
        self.logger.warning("No CPU temperature found in sensors output")
        return "未知"
    
    def extract_mobo_temp(self, sensors_output: str) -> str:
        """从 sensors 输出中提取主板温度（优化版）"""
        # 首先尝试解析JSON格式
        if sensors_output.strip().startswith('{'):
            try:
                data = json.loads(sensors_output)
                
                # 查找包含主板相关键名的温度值
                candidates = []
                for key, values in data.items():
                    if any(kw in key.lower() for kw in ["system", "motherboard", "mb", "board", "pch", "chipset", "sys", "baseboard"]):
                        for subkey, temp_value in values.items():
                            if any(kw in subkey.lower() for kw in ["temp", "input"]) and not "crit" in subkey.lower():
                                try:
                                    if isinstance(temp_value, (int, float)):
                                        candidates.append(temp_value)
                                        self.logger.debug("Found mobo temp candidate in JSON: %s/%s = %.1f°C", key, subkey, temp_value)
                                except Exception:
                                    pass
                
                # 如果有候选值，取平均值
                if candidates:
                    avg_temp = sum(candidates) / len(candidates)
                    return f"{avg_temp:.1f} °C"
            except Exception as e:
                self.logger.warning("Failed to parse sensors JSON: %s", str(e))
        
        # 如果JSON解析失败，使用正则表达式
        temp_values = []
        patterns = [
            r'SYSTIN:\s*\+?(\d+\.?\d*)°C',
            r'System Temp:\s*\+?(\d+\.?\d*)°C',
            r'MB Temperature:\s*\+?(\d+\.?\d*)°C',
            r'Motherboard:\s*\+?(\d+\.?\d*)°C',
            r'SYS Temp:\s*\+?(\d+\.?\d*)°C',
            r'Board Temp:\s*\+?(\d+\.?\d*)°C',
            r'PCH_Temp:\s*\+?(\d+\.?\d*)°C',
            r'Chipset:\s*\+?(\d+\.?\d*)°C',
            r'Baseboard Temp:\s*\+?(\d+\.?\d*)°C',
            r'asusec-isa-\S+:\s*\+?(\d+\.?\d*)°C',  # 华硕主板
            r'Tmotherboard:\s*\+?(\d+\.?\d*)°C',
            r'Temp2:\s*\+?(\d+\.?\d*)°C',  # 通用传感器
            r'System Temperature:\s*\+?(\d+\.?\d*)°C',
            r'MB_CPU_TEMP:\s*\+?(\d+\.?\d*)°C',
            r'Mainboard Temp:\s*\+?(\d+\.?\d*)°C'
        ]
        
        found_any = False
        for pattern in patterns:
            matches = re.finditer(pattern, sensors_output, re.IGNORECASE)
            for match in matches:
                try:
                    temp = float(match.group(1))
                    temp_values.append(temp)
                    found_any = True
                    self.logger.debug("Found motherboard temperature with pattern: %s: %.1f°C", pattern, temp)
                except (ValueError, IndexError):
                    continue
        
        # 如果有找到温度值，取平均值
        if temp_values:
            avg_temp = sum(temp_values) / len(temp_values)
            return f"{avg_temp:.1f} °C"
        
        # 如果所有模式都失败，尝试手动扫描所有温度值
        fallback_candidates = []
        for line in sensors_output.splitlines():
            if '°C' in line:
                # 跳过CPU相关的行
                if any(kw in line.lower() for kw in ["core", "cpu", "package", "tccd", "k10temp", "processor", "amd", "intel"]):
                    continue
                    
                # 跳过风扇和电压行
                if any(kw in line.lower() for kw in ["fan", "volt", "vin", "+3.3", "+5", "+12", "vdd"]):
                    continue
                    
                # 查找温度值
                match = re.search(r'(\d+\.?\d*)\s*°C', line)
                if match:
                    try:
                        temp = float(match.group(1))
                        # 合理温度范围检查 (0-80°C)
                        if 0 < temp < 80:
                            fallback_candidates.append(temp)
                            self.logger.debug("Fallback mobo candidate: %s -> %.1f°C", line.strip(), temp)
                    except ValueError:
                        continue
        
        # 如果有候选值，取平均值
        if fallback_candidates:
            avg_temp = sum(fallback_candidates) / len(fallback_candidates)
            self.logger.warning("Using fallback motherboard temperature detection")
            return f"{avg_temp:.1f} °C"
        
        self.logger.warning("No motherboard temperature found in sensors output")
        return "未知"
    
    async def reboot_system(self):
        """重启系统"""
        self.logger.info("Initiating system reboot...")
        try:
            await self.coordinator.run_command("sudo reboot")
            self.logger.info("Reboot command sent")
            
            # 更新系统状态为重启中
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
            await self.coordinator.run_command("sudo poweroff")
            self.logger.info("Shutdown command sent")
            
            # 立即更新系统状态为关闭
            if "system" in self.coordinator.data:
                self.coordinator.data["system"]["status"] = "off"
                self.coordinator.async_update_listeners()
        except Exception as e:
            self.logger.error("Failed to shutdown system: %s", str(e))
            raise