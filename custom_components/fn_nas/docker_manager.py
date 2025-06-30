import logging
import json
from typing import List, Dict

_LOGGER = logging.getLogger(__name__)

class DockerManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.logger = _LOGGER.getChild("docker_manager")
        self.logger.setLevel(logging.DEBUG)
        
    async def get_containers(self) -> List[Dict[str, str]]:
        """获取Docker容器列表及其状态"""
        try:
            # 使用docker命令获取容器列表，格式为JSON
            output = await self.coordinator.run_command("docker ps -a --format '{{json .}}'")
            self.logger.debug("Docker容器原始输出: %s", output)
            containers = []
            
            # 每行一个容器的JSON
            for line in output.splitlines():
                if not line.strip():
                    continue
                try:
                    # 解析JSON
                    container_info = json.loads(line)
                    # 提取所需字段
                    container = {
                        "id": container_info.get("ID", ""),
                        "name": container_info.get("Names", ""),
                        "status": container_info.get("State", "").lower(),
                        "image": container_info.get("Image", ""),
                    }
                    containers.append(container)
                except json.JSONDecodeError:
                    self.logger.warning("解析Docker容器信息失败: %s", line)
            return containers
        except Exception as e:
            self.logger.error("获取Docker容器列表失败: %s", str(e), exc_info=True)
            return []
    
    async def control_container(self, container_name, action):
        """控制容器操作"""
        valid_actions = ["start", "stop", "restart"]
        if action not in valid_actions:
            raise ValueError(f"无效操作: {action}")
        
        command = f"docker {action} {container_name}"
        try:
            await self.coordinator.run_command(command)
            return True
        except Exception as e:
            self.logger.error("执行Docker容器操作失败: %s", str(e), exc_info=True)
            return False