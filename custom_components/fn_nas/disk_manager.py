import re
import logging
import asyncio
import time
from .const import CONF_IGNORE_DISKS, CONF_CACHE_TIMEOUT, DEFAULT_CACHE_TIMEOUT

_LOGGER = logging.getLogger(__name__)

class DiskManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.logger = _LOGGER.getChild("disk_manager")
        self.logger.setLevel(logging.DEBUG)
        self.disk_status_cache = {}  # 缓存磁盘状态 {"sda": "活动中", ...}
        self.disk_full_info_cache = {}  # 缓存磁盘完整信息
        self.first_run = True  # 首次运行标志
        self.initial_detection_done = False  # 首次完整检测完成标志
        self.cache_expiry = {}  # 缓存过期时间（时间戳）
        # 获取缓存超时配置（分钟），转换为秒
        self.cache_timeout = self.coordinator.config.get(CONF_CACHE_TIMEOUT, DEFAULT_CACHE_TIMEOUT) * 60

    def extract_value(self, text: str, patterns, default="未知", format_func=None):
        if not text:
            return default
        
        if not isinstance(patterns, list):
            patterns = [patterns]
            
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            if matches:
                value = matches[0]
                try:
                    return format_func(value) if format_func else value.strip()
                except Exception as e:
                    self.logger.debug("Format error for value '%s': %s", value, str(e))
                    return value.strip()
        
        self.logger.debug("No match found for patterns: %s", patterns)
        return default
    
    async def check_disk_active(self, device: str, window: int = 30) -> bool:
        """检查硬盘在指定时间窗口内是否有活动"""
        try:
            stat_path = f"/sys/block/{device}/stat"
            
            # 读取统计文件
            stat_output = await self.coordinator.run_command(f"cat {stat_path} 2>/dev/null")
            if not stat_output:
                self.logger.debug(f"无法读取 {stat_path}，默认返回活跃状态")
                return True
                
            # 解析统计信息
            stats = stat_output.split()
            if len(stats) < 11:
                self.logger.debug(f"无效的统计信息格式：{stat_output}")
                return True
                
            # 关键字段：当前正在进行的I/O操作数量（第9个字段，索引8）
            in_flight = int(stats[8])
            
            # 如果当前有I/O操作，直接返回活跃状态
            if in_flight > 0:
                return True
                
            # 检查I/O操作时间（第10个字段，索引9） - io_ticks（单位毫秒）
            io_ticks = int(stats[9])
            
            # 如果设备在窗口时间内有I/O活动，返回活跃状态
            if io_ticks > window * 1000:
                return True
                
            # 所有检查都通过，返回非活跃状态
            return False
            
        except Exception as e:
            self.logger.error(f"检测硬盘活动状态失败: {str(e)}", exc_info=True)
            return True  # 出错时默认执行检测
    
    async def get_disk_activity(self, device: str) -> str:
        """获取硬盘活动状态（活动中/空闲中/休眠中）"""
        try:
            # 检查硬盘是否处于休眠状态
            state_path = f"/sys/block/{device}/device/state"
            state_output = await self.coordinator.run_command(f"cat {state_path} 2>/dev/null || echo 'unknown'")
            state = state_output.strip().lower()
            
            if state in ["standby", "sleep"]:
                return "休眠中"
            
            # 检查最近一分钟内的硬盘活动
            stat_path = f"/sys/block/{device}/stat"
            stat_output = await self.coordinator.run_command(f"cat {stat_path}")
            stats = stat_output.split()
            
            if len(stats) >= 11:
                recent_reads = int(stats[8])
                recent_writes = int(stats[9])
                
                if recent_reads > 0 or recent_writes > 0:
                    return "活动中"
            
            return "空闲中"
            
        except Exception as e:
            self.logger.error(f"获取硬盘 {device} 状态失败: {str(e)}", exc_info=True)
            return "未知"
    
    async def get_disks_info(self) -> list[dict]:
        disks = []
        try:
            # 清理过期缓存
            now = time.time()
            for device in list(self.disk_full_info_cache.keys()):
                if now - self.cache_expiry.get(device, 0) > self.cache_timeout:
                    self.logger.debug(f"磁盘 {device} 的缓存已过期，清除")
                    del self.disk_full_info_cache[device]
                    del self.cache_expiry[device]
            
            self.logger.debug("Fetching disk list...")
            lsblk_output = await self.coordinator.run_command("lsblk -dno NAME,TYPE")
            self.logger.debug("lsblk output: %s", lsblk_output)
            
            devices = []
            for line in lsblk_output.splitlines():
                if line:
                    parts = line.split()
                    if len(parts) >= 2:
                        devices.append({"name": parts[0], "type": parts[1]})
            
            self.logger.debug("Found %d block devices", len(devices))
            
            ignore_list = self.coordinator.config.get(CONF_IGNORE_DISKS, "").split(",")
            self.logger.debug("Ignoring disks: %s", ignore_list)
            
            for dev_info in devices:
                device = dev_info["name"]
                if device in ignore_list:
                    self.logger.debug("Skipping ignored disk: %s", device)
                    continue
                    
                if dev_info["type"] not in ["disk", "nvme", "rom"]:
                    self.logger.debug("Skipping non-disk device: %s (type: %s)", device, dev_info["type"])
                    continue
                
                device_path = f"/dev/{device}"
                disk_info = {"device": device}
                self.logger.debug("Processing disk: %s", device)
                
                # 获取硬盘状态（活动中/空闲中/休眠中）
                status = await self.get_disk_activity(device)
                disk_info["status"] = status
                
                # 更新状态缓存
                self.disk_status_cache[device] = status
                
                # 检查是否有缓存的完整信息
                cached_info = self.disk_full_info_cache.get(device, {})
                
                # 首次运行时强制获取完整信息
                if self.first_run:
                    self.logger.debug(f"首次运行，强制获取硬盘 {device} 的完整信息")
                    try:
                        # 执行完整的信息获取
                        await self._get_full_disk_info(disk_info, device_path)
                        # 更新缓存并设置过期时间
                        self.disk_full_info_cache[device] = disk_info.copy()
                        self.cache_expiry[device] = now
                    except Exception as e:
                        self.logger.warning(f"首次运行获取硬盘信息失败: {str(e)}", exc_info=True)
                        # 使用缓存信息（如果有）
                        disk_info.update(cached_info)
                        disk_info.update({
                            "model": "未知" if not cached_info.get("model") else cached_info["model"],
                            "serial": "未知" if not cached_info.get("serial") else cached_info["serial"],
                            "capacity": "未知" if not cached_info.get("capacity") else cached_info["capacity"],
                            "health": "检测失败" if not cached_info.get("health") else cached_info["health"],
                            "temperature": "未知" if not cached_info.get("temperature") else cached_info["temperature"],
                            "power_on_hours": "未知" if not cached_info.get("power_on_hours") else cached_info["power_on_hours"],
                            "attributes": cached_info.get("attributes", {})
                        })
                    disks.append(disk_info)
                    continue
                
                # 检查硬盘是否活跃
                is_active = await self.check_disk_active(device, window=30)
                if not is_active:
                    self.logger.debug(f"硬盘 {device} 处于非活跃状态，使用上一次获取的信息")
                    
                    # 优先使用缓存的完整信息
                    if cached_info:
                        disk_info.update({
                            "model": cached_info.get("model", "未检测"),
                            "serial": cached_info.get("serial", "未检测"),
                            "capacity": cached_info.get("capacity", "未检测"),
                            "health": cached_info.get("health", "未检测"),
                            "temperature": cached_info.get("temperature", "未检测"),
                            "power_on_hours": cached_info.get("power_on_hours", "未检测"),
                            "attributes": cached_info.get("attributes", {})
                        })
                    else:
                        # 如果没有缓存信息，使用默认值
                        disk_info.update({
                            "model": "未检测",
                            "serial": "未检测",
                            "capacity": "未检测",
                            "health": "未检测",
                            "temperature": "未检测",
                            "power_on_hours": "未检测",
                            "attributes": {}
                        })
                    
                    disks.append(disk_info)
                    continue
                
                try:
                    # 执行完整的信息获取
                    await self._get_full_disk_info(disk_info, device_path)
                    # 更新缓存并设置过期时间
                    self.disk_full_info_cache[device] = disk_info.copy()
                    self.cache_expiry[device] = now
                except Exception as e:
                    self.logger.warning(f"获取硬盘信息失败: {str(e)}", exc_info=True)
                    # 使用缓存信息（如果有）
                    disk_info.update(cached_info)
                    disk_info.update({
                        "model": "未知" if not cached_info.get("model") else cached_info["model"],
                        "serial": "未知" if not cached_info.get("serial") else cached_info["serial"],
                        "capacity": "未知" if not cached_info.get("capacity") else cached_info["capacity"],
                        "health": "检测失败" if not cached_info.get("health") else cached_info["health"],
                        "temperature": "未知" if not cached_info.get("temperature") else cached_info["temperature"],
                        "power_on_hours": "未知" if not cached_info.get("power_on_hours") else cached_info["power_on_hours"],
                        "attributes": cached_info.get("attributes", {})
                    })
                
                disks.append(disk_info)
                self.logger.debug("Processed disk %s: %s", device, disk_info)
            
            # 首次运行完成后标记
            if self.first_run:
                self.first_run = False
                self.initial_detection_done = True
                self.logger.info("首次磁盘检测完成")
            
            self.logger.info("Found %d disks after processing", len(disks))
            return disks
        
        except Exception as e:
            self.logger.error("Failed to get disk info: %s", str(e), exc_info=True)
            return []
    
    async def _get_full_disk_info(self, disk_info, device_path):
        """获取硬盘的完整信息（模型、序列号、健康状态等）"""
        # 获取基本信息
        info_output = await self.coordinator.run_command(f"smartctl -i {device_path}")
        self.logger.debug("smartctl -i output for %s: %s", disk_info["device"], info_output[:200] + "..." if len(info_output) > 200 else info_output)
        
        # 模型
        disk_info["model"] = self.extract_value(
            info_output, 
            [
                r"Device Model:\s*(.+)",
                r"Model(?: Family)?\s*:\s*(.+)",
                r"Model\s*Number:\s*(.+)"
            ]
        )
        
        # 序列号
        disk_info["serial"] = self.extract_value(
            info_output, 
            r"Serial Number\s*:\s*(.+)"
        )
        
        # 容量
        disk_info["capacity"] = self.extract_value(
            info_output, 
            r"User Capacity:\s*([^[]+)"
        )
        
        # 健康状态
        health_output = await self.coordinator.run_command(f"smartctl -H {device_path}")
        raw_health = self.extract_value(
            health_output,
            [
                r"SMART overall-health self-assessment test result:\s*(.+)",
                r"SMART Health Status:\s*(.+)"
            ],
            default="UNKNOWN"
        )

        # 健康状态中英文映射
        health_map = {
            "PASSED": "良好",
            "PASS": "良好",
            "OK": "良好",
            "GOOD": "良好",
            "FAILED": "故障",
            "FAIL": "故障",
            "ERROR": "错误",
            "WARNING": "警告",
            "CRITICAL": "严重",
            "UNKNOWN": "未知",
            "NOT AVAILABLE": "不可用"
        }

        # 转换为中文（不区分大小写）
        disk_info["health"] = health_map.get(raw_health.strip().upper(), "未知")
        
        # 获取详细数据
        data_output = await self.coordinator.run_command(f"smartctl -A {device_path}")
        self.logger.debug("smartctl -A output for %s: %s", disk_info["device"], data_output[:200] + "..." if len(data_output) > 200 else data_output)
        
        # 智能温度检测逻辑 - 处理多温度属性
        temp_patterns = [
            # 新增的NVMe专用模式
            r"Temperature:\s*(\d+)\s*Celsius",  # 匹配 NVMe 格式
            r"Composite:\s*\+?(\d+\.?\d*)°C",    # 匹配 NVMe 复合温度
            # 优先匹配属性194行（通常包含当前温度）
            r"194\s+Temperature_Celsius\s+.*?(\d+)\s*(?:$|$)",
            
            # 匹配其他温度属性
            r"\bTemperature_Celsius\b.*?(\d+)\b",
            r"Current Temperature:\s*(\d+)",
            r"Airflow_Temperature_Cel\b.*?(\d+)\b",
            r"Temp\s*[=:]\s*(\d+)"
        ]
        
        # 查找所有温度值
        temperatures = []
        for pattern in temp_patterns:
            matches = re.findall(pattern, data_output, re.IGNORECASE | re.MULTILINE)
            if matches:
                for match in matches:
                    try:
                        temperatures.append(int(match))
                    except ValueError:
                        pass
        
        # 优先选择属性194的温度值，如果没有则选择最大值
        if temperatures:
            # 优先选择属性194的值（如果存在）
            primary_match = re.search(r"194\s+Temperature_Celsius\s+.*?(\d+)\s*(?:\(|$)", 
                                    data_output, re.IGNORECASE | re.MULTILINE)
            if primary_match:
                disk_info["temperature"] = f"{primary_match.group(1)} °C"
            else:
                # 选择最高温度值（通常是当前温度）
                disk_info["temperature"] = f"{max(temperatures)} °C"
        else:
            disk_info["temperature"] = "未知"
        
        # 改进的通电时间检测逻辑 - 处理特殊格式
        power_on_hours = "未知"
        
        # 方法1：提取属性9的RAW_VALUE（处理特殊格式）
        attr9_match = re.search(
            r"^\s*9\s+Power_On_Hours\b[^\n]+\s+(\d+)h(?:\+(\d+)m(?:\+(\d+)\.\d+s)?)?",
            data_output, re.IGNORECASE | re.MULTILINE
        )
        if attr9_match:
            try:
                hours = int(attr9_match.group(1))
                # 如果有分钟部分，转换为小时的小数部分
                if attr9_match.group(2):
                    minutes = int(attr9_match.group(2))
                    hours += minutes / 60
                power_on_hours = f"{hours:.1f} 小时"
                self.logger.debug("Found power_on_hours via method1: %s", power_on_hours)
            except:
                pass
        
        # 方法2：如果方法1失败，尝试提取纯数字格式
        if power_on_hours == "未知":
            attr9_match = re.search(
                r"^\s*9\s+Power_On_Hours\b[^\n]+\s+(\d+)\s*$",
                data_output, re.IGNORECASE | re.MULTILINE
            )
            if attr9_match:
                try:
                    power_on_hours = f"{int(attr9_match.group(1))} 小时"
                    self.logger.debug("Found power_on_hours via method2: %s", power_on_hours)
                except:
                    pass
        
        # 方法3：如果前两种方法失败，使用原来的多模式匹配
        if power_on_hours == "未知":
            power_on_hours = self.extract_value(
                data_output,
                [
                    # 精确匹配属性9行
                    r"^\s*9\s+Power_On_Hours\b[^\n]+\s+(\d+)\s*$",
                    
                    # 通用匹配模式
                    r"9\s+Power_On_Hours\b.*?(\d+)\b",
                    r"Power_On_Hours\b.*?(\d+)\b",
                    r"Power On Hours\s+(\d+)",
                    r"Power on time\s*:\s*(\d+)\s*hours"
                ],
                default="未知",
                format_func=lambda x: f"{int(x)} 小时"
            )
            if power_on_hours != "未知":
                self.logger.debug("Found power_on_hours via method3: %s", power_on_hours)
        
        # 方法4：如果还没找到，尝试扫描整个属性表
        if power_on_hours == "未知":
            for line in data_output.split('\n'):
                if "Power_On_Hours" in line:
                    # 尝试提取特殊格式
                    match = re.search(r"(\d+)h(?:\+(\d+)m(?:\+(\d+)\.\d+s)?)?", line)
                    if match:
                        try:
                            hours = int(match.group(1))
                            if match.group(2):
                                minutes = int(match.group(2))
                                hours += minutes / 60
                            power_on_hours = f"{hours:.1f} 小时"
                            self.logger.debug("Found power_on_hours via method4 (special format): %s", power_on_hours)
                            break
                        except:
                            pass
                            
                    # 尝试提取纯数字
                    fields = line.split()
                    if fields and fields[-1].isdigit():
                        try:
                            power_on_hours = f"{int(fields[-1])} 小时"
                            self.logger.debug("Found power_on_hours via method4 (numeric): %s", power_on_hours)
                            break
                        except:
                            pass
        
        disk_info["power_on_hours"] = power_on_hours
        
        # 添加额外属性：温度历史记录
        temp_history = {}
        # 提取属性194的温度历史
        temp194_match = re.search(r"194\s+Temperature_Celsius+.*?\(\s*([\d\s]+)$", data_output)
        if temp194_match:
            try:
                values = [int(x) for x in temp194_match.group(1).split()]
                if len(values) >= 4:
                    temp_history = {
                        "最低温度": f"{values[0]} °C",
                        "最高温度": f"{values[1]} °C",
                        "当前温度": f"{values[2]} °C",
                        "阈值": f"{values[3]} °C" if len(values) > 3 else "N/A"
                    }
            except:
                pass
        
        # 保存额外属性
        disk_info["attributes"] = temp_history