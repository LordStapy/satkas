import asyncio
import logging
import os
import re
import sys
import pexpect
import time
from satkas.core.services.base_service import BaseService
from satkas.core.db.models import Setting

logger = logging.getLogger('rustykaspawallet')


class RustyKaspaWalletService(BaseService):
    service_icon = "wallet"
    icon_style = "kaspa"
    default_path = 'kaspa-wallet'

    def __init__(self, path=None):
        super().__init__()
        self.service_name = "Kaspa Wallet (Rusty Kaspa)"
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False
        self.binary_path = path if path else self.default_path
        self.wallet_name = ''
        self.wallet_password = ''

        # Get kaspad connection details from kaspad service settings
        self.kaspad_host = Setting.get_value("service.kaspad.host", 'kaspad.satkas.com')
        self.kaspad_port = Setting.get_value("service.kaspad.port", 16110)

        self.is_connected_to_node = False
        self.wallet_is_open = False
        self.process = None
        self.update_status_task = None
        # Define configuration mapping - Format 3
        # Note: kaspad_host and kaspad_port are automatically set from kaspad service settings
        self.configs = {
            'binary_path': {
                'attr': 'binary_path',
                'default': self.default_path,
                'fallback': None,
                'type': str,
                'env': 'RUSTY_KASPAWALLET_BINARY'
            },
            'wallet_name': {
                'attr': 'wallet_name',
                'default': self.wallet_name,
                'fallback': None,
                'type': str,
                'env': 'RUSTY_KASPAWALLET_NAME'
            },
            'wallet_password': {
                'attr': 'wallet_password',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'RUSTY_KASPAWALLET_PASSWORD'
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
        If kaspad settings changed and wallet is connected, reconnect.
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

        # Check if kaspad connection changed and wallet is connected
        kaspad_changed = (self.kaspad_host != old_kaspad_host or
                         self.kaspad_port != old_kaspad_port)

        if kaspad_changed and self.is_connected_to_node and self.is_enabled:
            print(f"Kaspad connection changed from {old_kaspad_host}:{old_kaspad_port} "
                  f"to {self.kaspad_host}:{self.kaspad_port}, reconnecting...")
            # Disconnect and reconnect with new settings
            asyncio.create_task(self._reconnect_for_new_kaspad())

    async def _reconnect_for_new_kaspad(self):
        """Reconnect to kaspad with new connection settings."""
        try:
            # Mark as not connected
            self.is_connected_to_node = False

            # Try to reconnect
            if await self.connect_to_node():
                print("Successfully reconnected to kaspad with new settings")
            else:
                print("Failed to reconnect to kaspad with new settings")
                self.is_detected = False
                self._notify_change(['is_detected'])

        except Exception as e:
            print(f"Error reconnecting for new kaspad settings: {e}")
            self.is_connected_to_node = False
            self.is_detected = False
            self._notify_change(['is_detected', 'is_connected_to_node'])

    @staticmethod
    def strip_ansi_codes(text):
        """Remove ANSI escape sequences and control characters from text"""
        if isinstance(text, bytes):
            text = text.decode('utf-8', errors='ignore')

        # Remove ANSI escape sequences (CSI, OSC, etc.)
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        text = ansi_escape.sub('', text)

        # Remove control characters that cause terminal corruption
        # Keep \n, \t but remove \r and other problematic chars
        # Remove also the "$ " pattern, we don't want it in the output
        text = re.sub(r'\$\s*', '', text)
        control_chars = re.compile(r'[\x00-\x08\x0B-\x0D\x0E-\x1F\x7F-\x9F]')
        text = control_chars.sub('', text)

        return text.strip('\n\r ')

    @staticmethod
    def safe_repr(data):
        """Create a safe repr that doesn't contain ANSI codes that could corrupt terminal"""
        if isinstance(data, bytes):
            # Replace ANSI escape sequences with safe markers
            safe_data = re.sub(rb'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', b'[ANSI]', data)
            # Replace control chars with safe markers
            safe_data = re.sub(rb'[\x00-\x1F\x7F-\x9F]', lambda m: f'[CTRL:{ord(m.group(0)):02X}]'.encode(), safe_data)
            return repr(safe_data)
        return repr(data)

    # async def start_wallet_pexpect(self):
    #     import pexpect
    #     self.process = pexpect.spawn(self.binary_path)
    #     # line = self.process.readline()
    #     # print(f"DEBUG: Line: {line}")
    #     self.process.expect_exact('$ ')
    #     # self.process.interact()
    #     self.process.send('help\r\n')
    #     self.process.expect_exact('$ ')
    #     print(self.process.before.decode('utf-8').replace('\n\r', '\n'))
    #     self.process.send('exit\r\n')
    #     self.process.expect_exact(pexpect.EOF)
    #     self.process.close()
    #     self.process = None

    async def start_wallet_process(self):
        p = pexpect.spawn(self.binary_path, echo=False)
        idx = await p.expect_exact(['$ ', pexpect.EOF], timeout=2, async_=True)
        # print(f"Start Wallet Index: {idx}")
        if idx == 0:
            self.process = p
        if idx == 1:
            print('process terminated')
            p.close()

    def stop_wallet_process(self):
        if self.process:
            # drain buffer first
            try:
                self.process.expect_exact('$ ', timeout=0.2, async_=False)
            except pexpect.exceptions.TIMEOUT:
                pass
            # await self.run_cmd('exit', pattern='bye!')
            self.process.send('exit\r\n')
            try:
                self.process.expect_exact(pexpect.EOF, timeout=0.5, async_=False)
                self.process.close()
            except pexpect.exceptions.TIMEOUT:
                self.process.kill(9)
            self.process = None
        
    async def run_cmd(self, cmd, pattern='\n\r$ ', timeout=3):
        if not self.process:
            await self.start_wallet_process()
            if not self.process:
                return False

        # Send the command
        try:
            self.process.send(f"{cmd}\r\n")
            idx = await self.process.expect_exact([pattern, pexpect.EOF], timeout=timeout, async_=True)
        except pexpect.exceptions.TIMEOUT:
            print(f"Timeout 0: {cmd}")
            idx = 0
        except pexpect.exceptions.OSError:
            self.is_detected = False
            self._notify_change(['is_detected'])
            idx = 1
        
        if idx == 0:
            out = self.strip_ansi_codes(self.process.before)
            if out == cmd:
                # expect more, we are just seeing the command echoed back
                try:
                    await self.process.expect_exact([pattern, pexpect.EOF], timeout=timeout, async_=True)
                except pexpect.exceptions.TIMEOUT:
                    print(f"Timeout 1: {cmd}")
            out = self.strip_ansi_codes(self.process.before)
            return out
        elif idx == 1:
            self.process.close()
            self.process = None
            return False

    async def detect(self):
        # Check if binary exists first
        if not os.path.exists(self.binary_path):
            self.is_detected = False
            self._notify_change(['is_detected'])
            return self.is_detected

        await self.start_wallet_process()
        if self.process:
            self.is_detected = True
            self._notify_change(['is_detected'])
        else:
            self.is_detected = False
            self._notify_change(['is_detected'])
        return self.is_detected
        
        # out = await self.run_cmd('connect', timeout=10)
        # out = await self.run_cmd('ping', timeout=10)
        # # await self.stop_wallet_process()
        # if 'ping ok' in out:
        #     self.is_detected = True
        # else:
        #     self.is_detected = False
        # return self.is_detected

    async def validate(self):
        if not self.process:
            await self.start_wallet_process()
        if not self.process:
            return False
        if not self.is_connected_to_node:
            await self.connect_to_node()
            # print(f"Connect: {out}")
        await self.run_cmd('ping', timeout=3)
        # print(f"Ping: {out}")
        await self.open_wallet()
        if not self.wallet_is_open:
            return False
        return True

    def enable(self):
        """Enable the wallet service"""
        if self.is_detected and not self.is_enabled:
            if not self.process:
                asyncio.create_task(self.start_wallet_process())
            if not self.wallet_is_open:
                asyncio.create_task(self.open_wallet())
            self.is_enabled = True
            self.service_status_string = "Wallet starting"
            self._notify_change(['is_enabled', 'service_status_string'])
            self.update_status_task = asyncio.create_task(self.update_status(run_once=False))

    def disable(self):
        """Disable the wallet service"""
        if self.update_status_task:
            self.update_status_task.cancel()
            # asyncio.get_running_loop().run_until_complete(self.update_status_task)
            self.update_status_task = None
        if self.process:
            self.stop_wallet_process()
        self.is_enabled = False
        self.service_status_string = "Wallet stopped"
        self._notify_change(['is_enabled', 'service_status_string'])

    async def update_status(self, run_once=True):
        while True:
            if self.is_detected:
                if self.wallet_is_open:
                    # read buffer to clear it, and parse the content
                    try:
                        buf = await self.process.expect_exact(['$ ', pexpect.EOF], timeout=1, async_=True)
                        buf = self.strip_ansi_codes(self.process.before)
                        #print(f"\nBuffer: {buf}")
                        try:
                            splitted = buf.split()
                            if buf.endswith('KAS)'):
                                balance = splitted[-4]
                                float(balance)
                                unconfirmed = splitted[-2][1:]
                                float(unconfirmed)
                                balance = f"{balance} (+{unconfirmed} unconfirmed)"
                            elif buf.endswith('KAS'):
                                balance = splitted[-2]
                                float(balance)
                            else:
                                print(f"Unknown buffer: {repr(buf)}")
                                balance = 'N/A'
                        except (IndexError, ValueError):
                            balance = 'N/A'
                        status_string = f"Balance: {balance} KAS"
                    except pexpect.exceptions.TIMEOUT:
                        # print('.', end='', flush=True)
                        if self.service_status_string == "Wallet starting" and self.wallet_is_open:
                            balance = await self.get_balance()
                            status_string = f"Balance: {balance} KAS"
                        else:
                            status_string = self.service_status_string
                    except asyncio.CancelledError:
                        return
                    if status_string != self.service_status_string:
                        self.service_status_string = status_string
                        self._notify_change(['service_status_string'])
                        print(f"\nStatus string: {status_string}")
                try:
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    return
            else:
                await self.detect()
                await asyncio.sleep(5)
            if run_once:
                break

    async def create_wallet(self):
        pass

    async def connect_to_node(self):
        res = await self.run_cmd('connect', timeout=10)
        print(f"Connect to node: {res}")
        if 'Connected to Kaspa node' in res:
            self.is_connected_to_node = True
        else:
            self.is_connected_to_node = False
        return self.is_connected_to_node

    async def get_server_node(self):
        res = await self.run_cmd('server')
        print(res)
        return True

    async def set_server_node(self, node=None):
        if node is None:
            node = f"{self.kaspad_host}:{self.kaspad_port}"
        res = await self.run_cmd(f'server {node}', timeout=10)
        print(f"Set server node: {res}")
        return True

    async def open_wallet(self):
        while not self.process:
            # print("Waiting for wallet to start")
            await asyncio.sleep(1)
        if not self.is_connected_to_node:
            if not await self.connect_to_node():
                print("Failed to connect to node")
                return False
        step1 = await self.run_cmd(f"wallet open {self.wallet_name}", pattern='password: ', timeout=5)
        # print(f"Step1: {step1}")
        if 'Enter wallet' not in step1:
            return False
        step2 = await self.run_cmd(self.wallet_password, pattern='KAS $ ')
        # print(f"Step2: {step2}")
        if self.wallet_name not in step2:
            return False
        self.wallet_is_open = True
        balance = await self.get_balance()
        self.service_status_string = f"Balance: {balance} KAS"
        self._notify_change(['service_status_string'])
        return True

    async def close_wallet(self):
        await self.run_cmd('wallet close', pattern='close\n\r$ ', timeout=1)
        self.wallet_is_open = False

    async def get_balance(self, retries=5):
        # print(f"Get balance: {self.process}, {self.wallet_is_open}")
        c = 0
        while not self.process or not self.wallet_is_open:
            await asyncio.sleep(1)
            c += 1
            if c >= retries:
                return 'N/A'
        res = await self.run_cmd('list', pattern='KAS $ ', timeout=2)
        balance = res.split()[-1]
        try:
            float(balance)
        except ValueError:
            balance = 'N/A'
        return balance

    async def get_address(self):
        out = await self.run_cmd('address', pattern='KAS $ ')
        try:
            address = [s for s in out.split() if 'kaspa:' in s][0].strip('$\n\r ')
        except IndexError:
            address = None
        # print(f"Address: {address}")
        return address

    async def get_new_address(self):
        return await self.get_address()

    async def send_transaction(self, address, amount):
        out = await self.run_cmd(f'send {address} {amount}', pattern='password: ')
        print(f"Send transaction: {out}")
        out2 = await self.run_cmd(self.wallet_password, pattern='KAS $ ', timeout=5)
        print(f"Send transaction 2: {out2}")

    async def pay(self, destination, amount, sm=None):
        logger.warning("rusty kaspa wallet support is experimental, use with caution")
        return await self.send_transaction(destination, amount)


async def main():
    service = RustyKaspaWalletService()
    await service.detect()
    print(f"Is detected: {service.is_detected}")
    await asyncio.sleep(1)
    service.enable()
    while not service.wallet_is_open:
        await asyncio.sleep(1)
    print('Wallet opened, send tx')
    await asyncio.sleep(300)
    sys.exit()
    input('Press Enter to continue...')
    print(f"Balance before send: {await service.get_balance()}")
    await service.get_address()
    await service.send_transaction('kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q', 1)
    print(f"Balance after send: {await service.get_balance()}")
    await service.close_wallet()
    print('Wallet closed')
    print(f"Balance after close: {await service.get_balance()}")
    await service.stop_wallet_process()
    print('Wallet process stopped')

if __name__ == '__main__':
    asyncio.run(main())