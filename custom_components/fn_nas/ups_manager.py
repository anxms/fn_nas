import logging
import re
import json
import os
from datetime import datetime
from .const import DOMAIN, UPS_INFO

_LOGGER = logging.getLogger(__name__)

class UPSManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.logger = _LOGGER.getChild("ups_manager")
        self.logger.setLevel(logging.DEBUG)
        self.debug_enabled = False  # UPS调试模式开关
        self.ups_debug_path = "/config/fn_nas_ups_debug"  # UPS调试文件保存路径
    
    async def get_ups_info(self) -> dict:
        """获取连接的UPS信息"""
        ups_info = {
            "status": "未知",
            "battery_level": "未知",
            "runtime_remaining": "未知",
            "input_voltage": "未知",
            "output_voltage": "未知",
            "load_percent": "未知",
            "model": "未知",
            "ups_type": "未知",
            "last_update": datetime.now().isoformat()
        }
        
        try:
            # 尝试使用NUT工具获取UPS信息
            self.logger.debug("尝试使用NUT工具获取UPS信息")
            output = await self.coordinator.run_command("upsc -l")
            
            if output and "No such file" not in output:
                # 获取第一个可用的UPS名称
                ups_names = output.splitlines()
                if ups_names:
                    ups_name = ups_names[0].strip()
                    self.logger.debug("发现UPS: %s", ups_name)
                    
                    # 获取详细的UPS信息
                    ups_details = await self.coordinator.run_command(f"upsc {ups_name}")
                    self.logger.debug("UPS详细信息: %s", ups_details)
                    
                    # 保存UPS数据以便调试
                    self.save_ups_data_for_debug(ups_details)
                    
                    # 解析UPS信息
                    return self.parse_nut_ups_info(ups_details)
                else:
                    self.logger.debug("未找到连接的UPS")
            else:
                self.logger.debug("未安装NUT工具，尝试备用方法")
            
            # 备用方法：尝试直接读取UPS状态
            return await self.get_ups_info_fallback()
            
        except Exception as e:
            self.logger.error("获取UPS信息时出错: %s", str(e), exc_info=True)
            return ups_info
    
    async def get_ups_info_fallback(self) -> dict:
        """备用方法获取UPS信息"""
        self.logger.info("尝试备用方法获取UPS信息")
        ups_info = {
            "status": "未知",
            "battery_level": "未知",
            "runtime_remaining": "未知",
            "input_voltage": "未知",
            "output_voltage": "未知",
            "load_percent": "未知",
            "model": "未知",
            "ups_type": "未知",
            "last_update": datetime.now().isoformat()
        }
        
        try:
            # 方法1: 检查USB连接的UPS
            usb_ups_output = await self.coordinator.run_command("lsusb | grep -i ups || echo 'No USB UPS'")
            if usb_ups_output and "No USB UPS" not in usb_ups_output:
                self.logger.debug("检测到USB UPS设备: %s", usb_ups_output)
                ups_info["ups_type"] = "USB"
                
                # 尝试从输出中提取型号
                model_match = re.search(r"ID\s+\w+:\w+\s+(.+)", usb_ups_output)
                if model_match:
                    ups_info["model"] = model_match.group(1).strip()
            
            # 方法2: 检查UPS服务状态
            service_output = await self.coordinator.run_command("systemctl status apcupsd || systemctl status nut-server || echo 'No UPS service'")
            if "active (running)" in service_output:
                ups_info["status"] = "在线"
            
            # 方法3: 尝试读取UPS电池信息
            battery_info = await self.coordinator.run_command("cat /sys/class/power_supply/*/capacity 2>/dev/null || echo ''")
            if battery_info and battery_info.strip().isdigit():
                try:
                    ups_info["battery_level"] = int(battery_info.strip())
                except (ValueError, TypeError):
                    pass
            
            # 创建带单位的字符串表示形式
            try:
                ups_info["battery_level_str"] = f"{ups_info['battery_level']}%" if isinstance(ups_info["battery_level"], int) else "未知"
            except KeyError:
                ups_info["battery_level_str"] = "未知"
            
            return ups_info
            
        except Exception as e:
            self.logger.error("备用方法获取UPS信息失败: %s", str(e))
            return ups_info
    
    def parse_nut_ups_info(self, ups_output: str) -> dict:
        """解析NUT工具输出的UPS信息"""
        ups_info = {
            "status": "未知",
            "battery_level": "未知",
            "runtime_remaining": "未知",
            "input_voltage": "未知",
            "output_voltage": "未知",
            "load_percent": "未知",
            "model": "未知",
            "ups_type": "NUT",
            "last_update": datetime.now().isoformat()
        }
        
        # 尝试解析键值对格式
        data = {}
        for line in ups_output.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                data[key.strip()] = value.strip()
        
        # 映射关键信息
        ups_info["model"] = data.get("ups.model", "未知")
        ups_info["status"] = self.map_ups_status(data.get("ups.status", "未知"))
        
        # 电池信息 - 转换为浮点数
        battery_charge = data.get("battery.charge")
        if battery_charge:
            try:
                ups_info["battery_level"] = float(battery_charge)
            except (ValueError, TypeError):
                pass
        
        # 剩余运行时间 - 转换为整数（分钟）
        runtime_left = data.get("battery.runtime")
        if runtime_left:
            try:
                minutes = int(runtime_left) // 60
                ups_info["runtime_remaining"] = minutes
            except (ValueError, TypeError):
                pass
        
        # 输入电压 - 转换为浮点数
        input_voltage = data.get("input.voltage")
        if input_voltage:
            try:
                ups_info["input_voltage"] = float(input_voltage)
            except (ValueError, TypeError):
                pass
        
        # 输出电压 - 转换为浮点数
        output_voltage = data.get("output.voltage")
        if output_voltage:
            try:
                ups_info["output_voltage"] = float(output_voltage)
            except (ValueError, TypeError):
                pass
        
        # 负载百分比 - 转换为浮点数
        load_percent = data.get("ups.load")
        if load_percent:
            try:
                ups_info["load_percent"] = float(load_percent)
            except (ValueError, TypeError):
                pass
        
        # 创建带单位的字符串表示形式
        try:
            ups_info["battery_level_str"] = f"{ups_info['battery_level']:.1f}%" if isinstance(ups_info["battery_level"], float) else "未知"
        except KeyError:
            ups_info["battery_level_str"] = "未知"
        
        try:
            ups_info["runtime_remaining_str"] = f"{ups_info['runtime_remaining']}分钟" if isinstance(ups_info["runtime_remaining"], int) else "未知"
        except KeyError:
            ups_info["runtime_remaining_str"] = "未知"
        
        try:
            ups_info["input_voltage_str"] = f"{ups_info['input_voltage']:.1f}V" if isinstance(ups_info["input_voltage"], float) else "未知"
        except KeyError:
            ups_info["input_voltage_str"] = "未知"
        
        try:
            ups_info["output_voltage_str"] = f"{ups_info['output_voltage']:.1f}V" if isinstance(ups_info["output_voltage"], float) else "未知"
        except KeyError:
            ups_info["output_voltage_str"] = "未知"
        
        try:
            ups_info["load_percent_str"] = f"{ups_info['load_percent']:.1f}%" if isinstance(ups_info["load_percent"], float) else "未知"
        except KeyError:
            ups_info["load_percent_str"] = "未知"
        
        return ups_info
    
    def map_ups_status(self, status_str: str) -> str:
        """映射UPS状态到中文"""
        status_map = {
            "OL": "在线",
            "OB": "电池供电",
            "LB": "电池电量低",
            "HB": "电池电量高",
            "RB": "需要更换电池",
            "CHRG": "正在充电",
            "DISCHRG": "正在放电",
            "BYPASS": "旁路模式",
            "CAL": "校准中",
            "OFF": "离线",
            "OVER": "过载",
            "TRIM": "电压调整中",
            "BOOST": "电压提升中",
            "FSD": "强制关机",
            "ALARM": "警报状态"
        }
        
        # 处理复合状态
        for key, value in status_map.items():
            if key in status_str:
                return value
        
        return status_str if status_str else "未知"
    
    def save_ups_data_for_debug(self, ups_output: str):
        """保存UPS数据以便调试"""
        if not self.debug_enabled:
            return
            
        try:
            # 创建调试目录
            if not os.path.exists(self.ups_debug_path):
                os.makedirs(self.ups_debug_path)
            
            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(self.ups_debug_path, f"ups_{timestamp}.log")
            
            # 写入文件
            with open(filename, "w") as f:
                f.write(ups_output)
            
            self.logger.info("保存UPS数据到 %s 用于调试", filename)
        except Exception as e:
            self.logger.error("保存UPS数据失败: %s", str(e))