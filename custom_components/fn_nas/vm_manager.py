import logging
import re
from asyncssh import SSHClientConnection

_LOGGER = logging.getLogger(__name__)

class VMManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.vms = []
        self.logger = _LOGGER.getChild("vm_manager")
        # 根据Home Assistant的日志级别动态设置
        self.logger.setLevel(logging.DEBUG if _LOGGER.isEnabledFor(logging.DEBUG) else logging.INFO)
        self.debug_enabled = _LOGGER.isEnabledFor(logging.DEBUG)

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

    async def get_vm_list(self):
        """获取虚拟机列表及其状态"""
        try:
            self._debug_log("开始获取虚拟机列表")
            output = await self.coordinator.run_command("virsh list --all")
            self._debug_log(f"virsh命令输出: {output}")
            
            self.vms = self._parse_vm_list(output)
            self._info_log(f"获取到{len(self.vms)}个虚拟机")
            return self.vms
        except Exception as e:
            self._error_log(f"获取虚拟机列表失败: {str(e)}")
            return []

    def _parse_vm_list(self, output):
        """解析虚拟机列表输出"""
        vms = []
        # 跳过标题行
        lines = output.strip().split('\n')[2:]
        for line in lines:
            if not line.strip():
                continue
            parts = line.split(maxsplit=2)  # 更健壮的解析方式
            if len(parts) >= 3:
                vm_id = parts[0].strip()
                name = parts[1].strip()
                state = parts[2].strip()
                vms.append({
                    "id": vm_id,
                    "name": name,
                    "state": state.lower(),
                    "title": ""  # 将在后续填充
                })
        return vms

    async def get_vm_title(self, vm_name):
        """获取虚拟机的标题"""
        try:
            self._debug_log(f"获取虚拟机{vm_name}的标题")
            output = await self.coordinator.run_command(f"virsh dumpxml {vm_name}")
            # 在XML输出中查找<title>标签
            match = re.search(r'<title>(.*?)</title>', output, re.DOTALL)
            if match:
                title = match.group(1).strip()
                self._debug_log(f"虚拟机{vm_name}标题: {title}")
                return title
            self._debug_log(f"虚拟机{vm_name}无标题，使用名称")
            return vm_name  # 如果没有标题，则返回虚拟机名称
        except Exception as e:
            self._error_log(f"获取虚拟机标题失败: {str(e)}")
            return vm_name

    async def control_vm(self, vm_name, action):
        """控制虚拟机操作"""
        valid_actions = ["start", "shutdown", "reboot"]
        if action not in valid_actions:
            raise ValueError(f"无效操作: {action}")
        
        command = f"virsh {action} {vm_name}"
        try:
            self._info_log(f"执行虚拟机操作: {command}")
            await self.coordinator.run_command(command)
            self._info_log(f"虚拟机{vm_name}操作{action}成功")
            return True
        except Exception as e:
            self._error_log(f"执行虚拟机操作失败: {str(e)}")
            return False