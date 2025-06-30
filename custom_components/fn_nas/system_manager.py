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
        """获取系统信息"""
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
        """从 sensors 输出中提取 CPU 温度，优先获取 Package id 0"""
        # 优先尝试获取 Package id 0 温度值
        package_id_pattern = r'Package id 0:\s*\+?(\d+\.?\d*)°C'
        package_match = re.search(package_id_pattern, sensors_output, re.IGNORECASE)
        if package_match:
            try:
                package_temp = float(package_match.group(1))
                self.logger.debug("优先使用 Package id 0 温度: %.1f°C", package_temp)
                return f"{package_temp:.1f} °C"
            except (ValueError, IndexError) as e:
                self.logger.debug("Package id 0 解析错误: %s", str(e))

        # 其次尝试解析JSON格式
        if sensors_output.strip().startswith('{'):
            try:
                data = json.loads(sensors_output)
                self.logger.debug("JSON sensors data: %s", json.dumps(data, indent=2))
                
                # 查找包含Package相关键名的温度值
                for key, values in data.items():
                    if any(kw in key.lower() for kw in ["package", "pkg", "physical"]):
                        for subkey, temp_value in values.items():
                            if any(kw in subkey.lower() for kw in ["temp", "input"]) and not "crit" in subkey.lower():
                                try:
                                    if isinstance(temp_value, (int, float)):
                                        self.logger.debug("JSON中找到Package温度: %s/%s = %.1f°C", key, subkey, temp_value)
                                        return f"{temp_value:.1f} °C"
                                except Exception as e:
                                    self.logger.debug("JSON值错误: %s", str(e))
                # 新增：尝试直接获取Tdie/Tctl温度（AMD CPU）
                for key, values in data.items():
                    if "k10temp" in key.lower():
                        for subkey, temp_value in values.items():
                            if "tdie" in subkey.lower() or "tctl" in subkey.lower():
                                try:
                                    if isinstance(temp_value, (int, float)):
                                        self.logger.debug("JSON中找到Tdie/Tctl温度: %s/%s = %.1f°C", key, subkey, temp_value)
                                        return f"{temp_value:.1f} °C"
                                except Exception:
                                    pass
            except Exception as e:
                self.logger.warning("JSON解析失败: %s", str(e))

        # 最后尝试其他模式
        other_patterns = [
            r'Package id 0:\s*\+?(\d+\.?\d*)°C',  # 再次尝试确保捕获
            r'CPU Temperature:\s*\+?(\d+\.?\d*)°C',
            r'cpu_thermal:\s*\+?(\d+\.?\d*)°C',
            r'Tdie:\s*\+?(\d+\.?\d*)°C',          # AMD CPU
            r'Tctl:\s*\+?(\d+\.?\d*)°C',          # AMD CPU
            r'PECI Agent \d:\s*\+?(\d+\.?\d*)°C',
            r'Composite:\s*\+?(\d+\.?\d*)°C',
            r'CPU\s+Temp:\s*\+?(\d+\.?\d*)°C',
            r'k10temp-pci\S*:\s*\+?(\d+\.?\d*)°C',
            r'Physical id 0:\s*\+?(\d+\.?\d*)°C'
        ]
        
        for pattern in other_patterns:
            match = re.search(pattern, sensors_output, re.IGNORECASE)
            if match:
                try:
                    temp = float(match.group(1))
                    self.logger.debug("匹配到CPU温度: %s: %.1f°C", pattern, temp)
                    return f"{temp:.1f} °C"
                except (ValueError, IndexError):
                    continue
        
        # 如果所有方法都失败返回未知
        return "未知"

    def extract_temp_from_systin(self, systin_data: dict) -> float:
        """从 SYSTIN 数据结构中提取温度值"""
        if not systin_data:
            return None
            
        # 尝试从不同键名获取温度值
        for key in ["temp1_input", "input", "value"]:
            temp = systin_data.get(key)
            if temp is not None:
                try:
                    return float(temp)
                except (TypeError, ValueError):
                    continue
        return None
    
    def extract_mobo_temp(self, sensors_output: str) -> str:
        """从 sensors 输出中提取主板温度"""
        # 首先尝试解析JSON格式
        if sensors_output.strip().startswith('{'):
            try:
                data = json.loads(sensors_output)
                
                # 查找包含主板相关键名的温度值
                candidates = []
                for key, values in data.items():
                    # 优先检查 SYSTIN 键
                    if "systin" in key.lower():
                        temp = self.extract_temp_from_systin(values)
                        if temp is not None:
                            return f"{temp:.1f} °C"
                    
                    if any(kw in key.lower() for kw in ["system", "motherboard", "mb", "board", "pch", "chipset", "sys", "baseboard", "systin"]):
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
                    
                # 新增：尝试直接获取 SYSTIN 的温度值
                systin_temp = self.extract_temp_from_systin(data.get("nct6798-isa-02a0", {}).get("SYSTIN", {}))
                if systin_temp is not None:
                    return f"{systin_temp:.1f} °C"
                    
            except Exception as e:
                self.logger.warning("Failed to parse sensors JSON: %s", str(e))
        
        # 改进SYSTIN提取逻辑
        systin_patterns = [
            r'SYSTIN:\s*[+\-]?\s*(\d+\.?\d*)\s*°C',  # 标准格式
            r'SYSTIN[:\s]+[+\-]?\s*(\d+\.?\d*)\s*°C',  # 兼容无冒号或多余空格
            r'System Temp:\s*[+\-]?\s*(\d+\.?\d*)\s*°C'  # 备选方案
        ]
        
        for pattern in systin_patterns:
            systin_match = re.search(pattern, sensors_output, re.IGNORECASE)
            if systin_match:
                try:
                    temp = float(systin_match.group(1))
                    self.logger.debug("Found SYSTIN temperature: %.1f°C", temp)
                    return f"{temp:.1f} °C"
                except (ValueError, IndexError) as e:
                    self.logger.debug("SYSTIN match error: %s", str(e))
                    continue
        for line in sensors_output.splitlines():
            if 'SYSTIN' in line or 'System Temp' in line:
                # 改进的温度值提取正则
                match = re.search(r'[+\-]?\s*(\d+\.?\d*)\s*°C', line)
                if match:
                    try:
                        temp = float(match.group(1))
                        self.logger.debug("Found mobo temp in line: %s: %.1f°C", line.strip(), temp)
                        return f"{temp:.1f} °C"
                    except ValueError:
                        continue
        
        
        # 如果找不到SYSTIN，尝试其他主板温度模式
        other_patterns = [
            r'System Temp:\s*\+?(\d+\.?\d*)°C',
            r'MB Temperature:\s*\+?(\d+\.?\d*)°C',
            r'Motherboard:\s*\+?(\d+\.?\d*)°C',
            r'SYS Temp:\s*\+?(\d+\.?\d*)°C',
            r'Board Temp:\s*\+?(\d+\.?\d*)°C',
            r'PCH_Temp:\s*\+?(\d+\.?\d*)°C',
            r'Chipset:\s*\+?(\d+\.?\d*)°C',
            r'Baseboard Temp:\s*\+?(\d+\.?\d*)°C',
            r'System Temperature:\s*\+?(\d+\.?\d*)°C',
            r'Mainboard Temp:\s*\+?(\d+\.?\d*)°C'
        ]
        
        temp_values = []
        for pattern in other_patterns:
            matches = re.finditer(pattern, sensors_output, re.IGNORECASE)
            for match in matches:
                try:
                    temp = float(match.group(1))
                    temp_values.append(temp)
                    self.logger.debug("Found motherboard temperature with pattern: %s: %.1f°C", pattern, temp)
                except (ValueError, IndexError):
                    continue
        
        # 如果有找到温度值，取平均值
        if temp_values:
            avg_temp = sum(temp_values) / len(temp_values)
            return f"{avg_temp:.1f} °C"
        
        # 最后，尝试手动扫描所有温度值
        fallback_candidates = []
        for line in sensors_output.splitlines():
            if '°C' in line:
                # 跳过CPU相关的行
                if any(kw in line.lower() for kw in ["core", "cpu", "package", "tccd", "k10temp", "processor", "amd", "intel", "nvme"]):
                    continue
                    
                # 跳过风扇和电压行
                if any(kw in line.lower() for kw in ["fan", "volt", "vin", "+3.3", "+5", "+12", "vdd", "power", "crit", "max", "min"]):
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
        
        # self.logger.warning("No motherboard temperature found in sensors output")
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
            await self.coordinator.run_command("sudo shutdown -h now")
            self.logger.info("Shutdown command sent")
            
            # 立即更新系统状态为关闭
            if "system" in self.coordinator.data:
                self.coordinator.data["system"]["status"] = "off"
                self.coordinator.async_update_listeners()
        except Exception as e:
            self.logger.error("Failed to shutdown system: %s", str(e))
            raise