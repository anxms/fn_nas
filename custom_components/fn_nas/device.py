import logging
from asyncssh import connect, SSHClientConnection
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class FeiniuNASDevice:
    def __init__(self, hass, config):
        self.hass = hass
        self.host = config[CONF_HOST]
        self.port = config[CONF_PORT]
        self.username = config[CONF_USERNAME]
        self.password = config[CONF_PASSWORD]
        self.mac = config[CONF_MAC]
        self.connection = None

    async def connect(self):
        try:
            self.connection = await connect(
                self.host,
                self.port,
                username=self.username,
                password=self.password,
                known_hosts=None
            )
            return True
        except Exception as e:
            _LOGGER.error("Connection error: %s", str(e))
            return False

    async def disconnect(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    async def run_command(self, command):
        try:
            if not self.connection or self.connection.is_closing():
                await self.connect()
            
            result = await self.connection.run(command)
            return result.stdout
        except Exception as e:
            _LOGGER.error("Command error: %s", str(e))
            return None

    async def get_hdd_info(self):
        output = await self.run_command("sudo smartctl --scan | awk '{print $1}'")
        disks = output.strip().split('\n') if output else []
        
        hdds = []
        for disk in disks:
            info = {}
            disk = disk.strip()
            if not disk:
                continue
            
            # 获取型号和温度
            smart_data = await self.run_command(f"sudo smartctl -a {disk}")
            if smart_data:
                for line in smart_data.split('\n'):
                    if "Model Family" in line or "Device Model" in line:
                        info['model'] = line.split(':')[1].strip()
                    elif "Temperature_Celsius" in line:
                        parts = line.split()
                        if len(parts) > 9:
                            info['temp'] = parts[9]
                # 获取健康状态
                health = await self.run_command(f"sudo smartctl -H {disk} | grep 'SMART overall-health'")
                info['health'] = health.split(':')[1].strip() if health else 'UNKNOWN'
                
                if 'model' in info:
                    hdds.append(info)
        return hdds


    async def reboot(self):
        await self.run_command("sudo reboot")

    async def shutdown(self):
        await self.run_command("sudo shutdown -h now")
        # 更新开关状态
        for entity in self.hass.data[DOMAIN].get('power_switch', []):
            entity.is_on = False
            entity.async_write_ha_state()

