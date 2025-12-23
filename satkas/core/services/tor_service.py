
import asyncio

from aiohttp_socks import ProxyConnector
from aiohttp import ClientSession, ClientTimeout

from satkas.core.services.base_service import BaseService


class TorService(BaseService):
    default_host = '127.0.0.1'
    default_port = 9050

    def __init__(self, host=None, port=None):
        super().__init__()
        self.service_name = 'Tor'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        # Define configuration mapping - Format 3: {'attr': ..., 'default': ..., 'fallback': ..., 'type': ..., 'env': ...}
        self.configs = {
            'host': {
                'attr': 'host',
                'default': self.default_host,
                'fallback': None,
                'type': str,
                'env': 'TOR_HOST'
            },
            'port': {
                'attr': 'port',
                'default': self.default_port,
                'fallback': None,
                'type': int,
                'env': 'TOR_PORT'
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
        socks_url = f"socks5://{self.host}:{self.port}"
        # print(socks_url)
        try:
            ProxyConnector.from_url(socks_url, rdns=True)
            self.is_detected = True
        except Exception as e:
            print('ProxyConnector error')
            print(e)
            self.is_detected = False
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        socks_url = f"socks5://{self.host}:{self.port}"
        connector = ProxyConnector.from_url(socks_url, rdns=True)
        timeout = ClientTimeout(total=10.0)
        try:
            async with ClientSession(connector=connector, timeout=timeout) as session:
                url = 'http://exlg6u3252bnzit7mgia3tpb2yctafmo3wbev72pwmxeqg7jiywsx2yd.onion'
                async with session.get(url) as res:
                    await res.text()
                    validated = True
        except Exception as e:
            print('Error connecting to test hidden service')
            print(e)
            validated = False
        return validated

    def enable(self):
        self.is_enabled = True
        self.update_status_task = asyncio.create_task(self.update_status())
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            self.update_status_task.cancel()
            # asyncio.get_running_loop().run_until_complete(self.update_status_task)
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    def on_disable(self):
        pass

    async def update_status(self, run_once=True):
        if self.is_enabled and self.is_detected:
            self.service_status_string = 'Connected'
        else:
            self.service_status_string = 'Disabled'
        self._notify_change(['service_status_string'])
        self.update_status_task = None
        
    def status(self):
        print(f"Name: {self.service_name}\nEnabled: {self.is_enabled}")


if __name__ == '__main__':
    import asyncio
    asyncio.run(TorService().detect())
