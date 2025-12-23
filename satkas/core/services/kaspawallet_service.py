
import asyncio
from satkas.core.services.base_service import BaseService
from satkas.core.db.models import Setting


class KaspawalletService(BaseService):
    default_path = 'kaspawallet'

    def __init__(self, path=None):
        super().__init__()
        self.service_name = "Kaspawallet (Go)"
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False
        self.binary_path = path if path else self.default_path
        self.wallet_file_path = ''
        self.wallet_password = ''

        # Get kaspad connection details from kaspad service settings
        self.kaspad_host = Setting.get_value("service.kaspad.host", 'kaspad.satkas.com')
        self.kaspad_port = Setting.get_value("service.kaspad.port", 16110)

        self.daemon_host = '127.0.0.1'
        self.daemon_port = 8082
        self.internal_daemon = False
        self.internal_daemon_task = None
        self.internal_daemon_process = None
        self.update_status_task = None
        # Define configuration mapping - Format 3
        # Note: kaspad_host and kaspad_port are automatically set from kaspad service settings
        self.configs = {
            'binary_path': {
                'attr': 'binary_path',
                'default': self.default_path,
                'fallback': None,
                'type': str,
                'env': 'KASPAWALLET_BINARY'
            },
            'wallet_file_path': {
                'attr': 'wallet_file_path',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'KASPAWALLET_FILE'
            },
            'wallet_password': {
                'attr': 'wallet_password',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'KASPAWALLET_PASSWORD'
            },
            'daemon_host': {
                'attr': 'daemon_host',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'KASPAWALLET_DAEMON_HOST'
            },
            'daemon_port': {
                'attr': 'daemon_port',
                'default': None,
                'fallback': None,
                'type': int,
                'env': 'KASPAWALLET_DAEMON_PORT'
            },
            'internal_daemon': {
                'attr': 'internal_daemon',
                'default': None,
                'fallback': None,
                'type': bool,
                'env': 'KASPAWALLET_INTERNAL_DAEMON'
            }
        }
        self.load_config()
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text):
        self.binary_path = text

    @property
    def config_string(self):
        return f"{self.binary_path}"

    def reload_config(self):
        """
        Reload configuration and handle kaspad connection changes.
        If kaspad settings changed and daemon is running, restart it.
        """
        # Store old kaspad connection details
        old_kaspad_host = self.kaspad_host
        old_kaspad_port = self.kaspad_port

        # Reload configuration from configs
        super().reload_config()

        # Also reload kaspad connection details from settings (they're not in configs)
        new_kaspad_host = Setting.get_value("service.kaspad.host", 'kaspad.satkas.com')
        new_kaspad_port = Setting.get_value("service.kaspad.port", 16110)

        # Update attributes
        self.kaspad_host = new_kaspad_host
        self.kaspad_port = new_kaspad_port

        # Check if kaspad connection changed and daemon is running
        kaspad_changed = (self.kaspad_host != old_kaspad_host or
                         self.kaspad_port != old_kaspad_port)

        if kaspad_changed and self.internal_daemon and self.is_enabled:
            print(f"Kaspad connection changed from {old_kaspad_host}:{old_kaspad_port} "
                  f"to {self.kaspad_host}:{self.kaspad_port}, restarting daemon...")
            # Disable and re-enable to restart with new settings
            asyncio.create_task(self._restart_daemon_for_new_kaspad())

    async def _restart_daemon_for_new_kaspad(self):
        """Restart the daemon with new kaspad connection settings."""
        try:
            # Stop current daemon
            if self.internal_daemon_process:
                self.internal_daemon_process.terminate()
                try:
                    await asyncio.wait_for(self.internal_daemon_process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.internal_daemon_process.kill()
                self.internal_daemon_process = None

            # Cancel any existing daemon task
            if self.internal_daemon_task:
                self.internal_daemon_task.cancel()
                self.internal_daemon_task = None

            # Start daemon with new settings
            self.internal_daemon_task = asyncio.create_task(self.start_internal_daemon())

        except Exception as e:
            print(f"Error restarting daemon for new kaspad settings: {e}")
            self.is_enabled = False
            self._notify_change(['is_enabled'])

    @staticmethod
    async def run(cmd):
        p = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await p.communicate()
        return out, err

    async def start_internal_daemon(self):
        self.internal_daemon_process = await asyncio.create_subprocess_exec(
            f"{self.binary_path}", 'start-daemon',
            '-f', self.wallet_file_path,
            '-s', f"{self.kaspad_host}:{self.kaspad_port}",
            '-l', f"{self.daemon_host}:{self.daemon_port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await self.internal_daemon_process.wait()
        # out, err = await self.internal_daemon_process.communicate()
        # print(self.internal_daemon_process.pid, out, err)
        # await asyncio.sleep(5)

    async def detect(self):
        out, err = await self.run(f"{self.binary_path} version")
        out = out.decode().strip()
        if 'kaspawallet version' in out:
            self.is_detected = True
        else:
            self.is_detected = False
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        # ToDo: manage validation rules for go kaspawallet
        return True

    def enable(self):
        if self.internal_daemon:
            self.internal_daemon_task = asyncio.create_task(self.start_internal_daemon())
        self.is_enabled = True
        self.update_status_task = asyncio.create_task(self.update_status(run_once=False))
        self._notify_change(['is_enabled'])

    def disable(self):
        if self.update_status_task:
            self.update_status_task.cancel()
            # asyncio.get_running_loop().run_until_complete(self.update_status_task)
            self.update_status_task = None
        if self.internal_daemon_process:
            self.internal_daemon_process.terminate()
            # print(self.internal_daemon_process)
            # print(self.internal_daemon_process.returncode)
            self.internal_daemon_process = None
        if self.internal_daemon_task:
            self.internal_daemon_task.cancel()
            self.internal_daemon_task = None
        self.is_enabled = False
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once=True):
        while True:
            if self.is_detected:
                try:
                    balance = await self.get_balance()
                    status_string = f"Daemon: {self.daemon_host}:{self.daemon_port}\nBalance: {balance} KAS"
                except Exception as e:
                    status_string = 'Error getting balance'
                    self.is_detected = False
                    self._notify_change(['is_detected'])
                if status_string != self.service_status_string:
                    self.service_status_string = status_string
                    self._notify_change(['service_status_string'])
                await asyncio.sleep(10)
            else:
                await self.detect()
                await asyncio.sleep(1)
            if run_once:
                break

    async def get_new_address(self):
        out, err = await self.run(f"{self.binary_path} new-address -d {self.daemon_host}:{self.daemon_port}")
        try:
            out = out.decode().strip()
            address = out.split(':', maxsplit=1)[1].strip()
            print(f"New address: {address}")
            return address
        except Exception as e:
            print(f"Error getting new address: {e}")
            return False

    async def get_balance(self):
        out, err = await self.run(f"{self.binary_path} balance -d {self.daemon_host}:{self.daemon_port}")
        out = out.decode().strip()
        try:
            balance = out.split()[-1]
            try:
                float(balance)
            except ValueError:
                balance = 0
            # if not balance.isdigit():
            #     balance = 0
        except:
            # print(err)
            if 'kaspawallet daemon is not running' in err.decode():
                self.is_enabled = False
                self._notify_change(['is_enabled'])
            balance = 0
        return balance

    async def pay(self, destination, amount):
        out, err = await self.run(
            f"{self.binary_path} send -d {self.daemon_host}:{self.daemon_port} -t {destination} -v {amount} "
            f"-p {self.wallet_password} -f {self.wallet_file_path}")
        out = out.decode().strip()
        print(f"Pay result: {out}")
        print(f"Pay error: {err.decode()}")
        return out