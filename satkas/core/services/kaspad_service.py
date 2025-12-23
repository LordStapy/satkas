
import asyncio
import os

from satkas.core.services.base_service import BaseService
from satkas.core.klib.kgrpc import getBlockDagInfo


class KaspadService(BaseService):
    default_host = '127.0.0.1'
    default_port = 16110
    fallback_host = 'kaspad.satkas.com'
    fallback_port = 16110

    def __init__(self, host=None, port=None):
        super().__init__()
        self.service_name = "Kaspad"
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        # Define configuration mapping - Format 3
        self.configs = {
            'host': {
                'attr': 'host',
                'default': self.default_host,
                'fallback': self.fallback_host,
                'type': str,
                'env': 'KASPAD_HOST'
            },
            'port': {
                'attr': 'port',
                'default': self.default_port,
                'fallback': self.fallback_port,
                'type': int,
                'env': 'KASPAD_PORT'
            }
        }

        # Load settings from database or use provided values or defaults
        if host is None and port is None:
            self.load_config()
        else:
            self.host = host if host else self.default_host
            self.port = port if port else self.default_port

        self.update_status_task = None
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text):
        if ':' in text:
            self.host, port = text.split(':')
            self.port = int(port)
        elif text == '':
            self.host = self.default_host
            self.port = self.default_port
        else:
            self.host = text
            self.port = self.default_port

    @property
    def config_string(self):
        return f"{self.host}:{self.port}"

    async def detect(self):
        try:
            res = getBlockDagInfo(rpc_server=f"{self.host}:{self.port}", timeout=1)
            # print(f"Kaspad Service detect response: {res}")
            self.is_detected = True
        except Exception as e:
            self.is_detected = False
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        return self.is_detected

    def enable(self):
        self.is_enabled = True
        os.environ['KAS_RPC_SERVER'] = f"{self.host}:{self.port}"
        self.update_status_task = asyncio.create_task(self.update_status(run_once=False))
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            # print(f"Update status task: {self.update_status_task.get_coro()}")
            self.update_status_task.cancel()
            # loop = asyncio.get_running_loop()
            # print(f"Loop: {loop}")
            # task = asyncio.run_coroutine_threadsafe(self.update_status_task(), loop)
            # result = task.result()
            # print(f"Result: {result}")
            # print(f"Task: {task}")
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once=True):
        while True:
            if self.is_detected and self.is_enabled:
                try:
                    dag_info = getBlockDagInfo(rpc_server=f"{self.host}:{self.port}")
                    network_name = dag_info.get('networkName', '')
                    daa_score = dag_info.get('virtualDaaScore', '')
                    status_string = f"Network: {network_name}\nDAA Score: {daa_score}"
                except Exception as e:
                    status_string = 'Error getting DAG info'
                    self.is_detected = False
                    self._notify_change(['is_detected'])
                if status_string != self.service_status_string:
                    self.service_status_string = status_string
                    self._notify_change(['service_status_string'])
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    return
            else:
                await self.detect()
                await asyncio.sleep(1)
            if run_once:
                break


if __name__ == '__main__':
    import asyncio
    asyncio.run(KaspadService().detect())
