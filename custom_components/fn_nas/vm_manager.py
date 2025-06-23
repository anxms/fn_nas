import logging
import re
from asyncssh import SSHClientConnection

_LOGGER = logging.getLogger(__name__)

class VMManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.vms = []

    async def get_vm_list(self):
        """获取虚拟机列表及其状态"""
        try:
            output = await self.coordinator.run_command("virsh list --all")
            self.vms = self._parse_vm_list(output)
            return self.vms
        except Exception as e:
            _LOGGER.error("获取虚拟机列表失败: %s", str(e))
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
            output = await self.coordinator.run_command(f"virsh dumpxml {vm_name}")
            # 在XML输出中查找<title>标签
            match = re.search(r'<title>(.*?)</title>', output, re.DOTALL)
            if match:
                return match.group(1).strip()
            return vm_name  # 如果没有标题，则返回虚拟机名称
        except Exception as e:
            _LOGGER.error("获取虚拟机标题失败: %s", str(e))
            return vm_name

    async def control_vm(self, vm_name, action):
        """控制虚拟机操作"""
        valid_actions = ["start", "shutdown", "reboot"]
        if action not in valid_actions:
            raise ValueError(f"无效操作: {action}")
        
        command = f"virsh {action} {vm_name}"
        try:
            await self.coordinator.run_command(command)
            return True
        except Exception as e:
            _LOGGER.error("执行虚拟机操作失败: %s", str(e))
            return False