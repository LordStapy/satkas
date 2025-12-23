
import asyncio
import os
import json
import aiohttp
from satkas.core.services.base_service import BaseService


class LNBitsService(BaseService):
    default_base_url = 'http://127.0.0.1:5000'
    fallback_base_url = 'https://lnbits.satkas.com'

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.service_name = 'LNBits'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False
        self.base_url = kwargs.get('base_url', os.getenv('LNBITS_ENDPOINT', ''))
        self.user_id = kwargs.get('user_id', os.getenv('LNBITS_USER_ID', ''))
        self.read_api_key = kwargs.get('read_api_key', os.getenv('LNBITS_READ_KEY', ''))
        self.admin_api_key = kwargs.get('admin_api_key', os.getenv('LNBITS_ADMIN_KEY', ''))
        self.wallet_id = kwargs.get('wallet_id', os.getenv('LNBITS_WALLET_ID', ''))
        self.update_status_task = None
        # Define configuration mapping - Format 3
        self.configs = {
            'base_url': {
                'attr': 'base_url',
                'default': self.default_base_url,
                'fallback': self.fallback_base_url,
                'type': str,
                'env': 'LNBITS_ENDPOINT'
            },
            'user_id': {
                'attr': 'user_id',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'LNBITS_USER_ID'
            },
            'read_api_key': {
                'attr': 'read_api_key',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'LNBITS_READ_KEY'
            },
            'admin_api_key': {
                'attr': 'admin_api_key',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'LNBITS_ADMIN_KEY'
            },
            'wallet_id': {
                'attr': 'wallet_id',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'LNBITS_WALLET_ID'
            }
        }
        if not kwargs.get('skip_load_config', False):
            self.load_config()
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text):
        if not text.startswith("https"):
            text = f"https://{text}"
        self.base_url = text

    @property
    def config_string(self):
        return self.base_url if self.base_url else ''

    async def detect(self):
        try:
            res = await self.req('api/v1/health', 'GET')
            if res.get('server_time', None):
                self.is_detected = True
        except:
            self.is_detected = False
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        wallet_res = await self.get_wallet()
        wallet_balance = wallet_res.get('balance', None)
        if wallet_balance is None:
            return False
        return True

    def enable(self):
        self.is_enabled = True
        self.update_status_task = asyncio.create_task(self.update_status(run_once=False))
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            self.update_status_task.cancel()
            # asyncio.get_running_loop().run_until_complete(self.update_status_task)
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def on_disable(self):
        pass

    async def update_status(self, run_once=True):
        while True:
            if self.is_detected:
                try:
                    wallet_res = await self.get_wallet()
                    balance_sat = wallet_res.get('balance', 0) // 1000
                    status_string = f"Server: {self.base_url}\nBalance: {balance_sat} sats"
                except Exception as e:
                    status_string = 'Error getting wallet info'
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

    async def req(self, endpoint='', method='POST',  **kwargs):
        async with aiohttp.ClientSession() as session:
            async with session.request(method, f"{self.base_url}/{endpoint}", **kwargs) as response:
                res = await response.json()
        return res

    async def create_new_account(self):
        headers = {'Content-type': 'application/json'}
        payload = json.dumps({'name': 'satkas'})
        res = await self.req('api/v1/account', headers=headers, data=payload)
        self.user_id = res.get('user', None)
        self.read_api_key = res.get('inkey', None)
        self.admin_api_key = res.get('adminkey', None)
        self.wallet_id = res.get('id', None)
        print(f"[lnbits] Account created: {res}")

    async def get_wallet(self):
        headers = {'X-Api-Key': self.read_api_key, 'Content-type': 'application/json'}
        res = await self.req('api/v1/wallet', 'GET', headers=headers)
        # print(res)
        return res

    async def create_invoice(self, amount=0, memo=''):
        headers = {'X-Api-Key': self.read_api_key, 'Content-type': 'application/json'}
        payload = json.dumps({'out': False, 'amount': amount, 'memo': memo, 'unit': 'sat'})
        res = await self.req('api/v1/payments', headers=headers, data=payload)
        print(f"[lnbits] Created invoice: {res}")
        bolt11 = res.get('bolt11', None)
        return bolt11

    async def decode_invoice(self, invoice):
        headers = {'Content-type': 'application/json'}
        payload = json.dumps({
            'data': invoice,
            'filter_fields': ['amount_msat', 'date', 'expiry', 'payment_hash', 'description', 'payee']
        })
        res = await self.req('api/v1/payments/decode', headers=headers, data=payload)
        return res

    async def validate_invoice(self, invoice, parsed_data=None):
        if parsed_data is None:
            parsed_data = await self.decode_invoice(invoice)
        # TODO: invoice validation, we return True for now
        # list of checks to do: 
        # 1. expiration time must be in the future, but not too far in the future (based on settings' max expiry time)
        # 2. payee (destination) must be different than the source, unless we are validating an invoice created by us
        return True

    async def pay_invoice(self, invoice):
        headers = {'X-Api-Key': self.admin_api_key, 'Content-Type': 'application/json'}
        payload = json.dumps({'out': True, 'bolt11': invoice})
        res = await self.req('api/v1/payments', headers=headers, data=payload)
        #print(res)
        if not res.get('preimage', None):
            # check invoice status
            for i in range(30):
                await asyncio.sleep(1)
                status = await self.check_invoice(res.get('payment_hash', None))
                if status.get('preimage', None):
                    break
                print(f"[lnbits] Invoice status: {status}")
            else:
                return res
        return res

    async def check_invoice(self, payment_hash):
        headers = {'X-Api-Key': self.read_api_key}
        res = await self.req(f"api/v1/payments/{payment_hash}", 'GET', headers=headers)
        print(res)
        return res


async def main(cls):
    # await cls.create_new_account()
    print(await cls.detect())
    print(await cls.get_wallet())
    invoice = await cls.create_invoice(100, '')
    print(f"Invoice: {invoice}")

if __name__ == '__main__':
    import asyncio
    lnbits = LNBitsService(
        base_url=LNBitsService.fallback_base_url,
        skip_load_config=True
    )
    asyncio.run(main(lnbits))
