
import asyncio
import logging
import math
import os
import json
import aiohttp
from satkas.core.services.base_service import BaseService, PaymentStatus

logger = logging.getLogger('lnbits')


class LNBitsService(BaseService):
    service_icon = "lightning-bolt"
    icon_style = "lnbits"
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
        self._probe_task = None
        self._node_pubkey = None
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
        self._probe_task = asyncio.create_task(self._probe_node_pubkey())
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        self._node_pubkey = None
        for task in (self.update_status_task, self._probe_task):
            if task:
                task.cancel()
        self.update_status_task = None
        self._probe_task = None
        self._notify_change(['is_enabled'])

    async def on_disable(self):
        pass

    async def update_status(self, run_once=True):
        while True:
            if self.is_detected:
                try:
                    balance_sat = await self.get_balance()
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
        logger.info(f"[lnbits] Account created: {res}")

    async def get_wallet(self):
        headers = {'X-Api-Key': self.read_api_key, 'Content-type': 'application/json'}
        res = await self.req('api/v1/wallet', 'GET', headers=headers)
        # print(res)
        return res

    async def get_balance(self):
        """Wallet balance in sats."""
        wallet_res = await self.get_wallet()
        return wallet_res.get('balance', 0) // 1000

    async def create_invoice(self, amount=0, memo=''):
        headers = {'X-Api-Key': self.read_api_key, 'Content-type': 'application/json'}
        payload = json.dumps({'out': False, 'amount': amount, 'memo': memo, 'unit': 'sat'})
        res = await self.req('api/v1/payments', headers=headers, data=payload)
        logger.debug(f"[lnbits] Created invoice: {res}")
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
            # Poll for the preimage. check_payment, not check_invoice: what we
            # are waiting on here is the payment we just sent.
            payment_hash = res.get('payment_hash', None)
            for i in range(30):
                await asyncio.sleep(1)
                status = await self.check_payment(payment_hash)
                if status.get('preimage', None):
                    res['preimage'] = status['preimage']
                    break
                if status.get('status') == PaymentStatus.FAILED:
                    return res
                logger.debug(f"[lnbits] Payment status: {status}")
            else:
                return res
        return res

    async def check_invoice(self, payment_hash):
        headers = {'X-Api-Key': self.read_api_key}
        res = await self.req(f"api/v1/payments/{payment_hash}", 'GET', headers=headers)
        logger.debug(res)
        return res

    async def check_payment(self, payment_hash, timeout=10):
        """Status of an outgoing payment, as a PaymentStatus and a preimage.

        Same endpoint as check_invoice, because LNbits stores an invoice and a
        payment as one Payment record, but a different question: `amount` is
        signed msat, negative for outgoing, so a record that is not ours to
        have sent is reported as UNKNOWN rather than mistaken for our payment.

        timeout is part of the shared signature; this backend answers at once.
        """
        if not payment_hash:
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        try:
            res = await self.check_invoice(payment_hash)
        except Exception as e:
            logger.error(f"[lnbits] check_payment failed: {e}")
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        if not isinstance(res, dict):
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        # LNbits answers 404-ish payloads with a detail key; it has no record,
        # so nothing was sent.
        if res.get('detail') and not res.get('status') and res.get('paid') is None:
            return {'status': PaymentStatus.FAILED, 'preimage': None}

        amount = res.get('amount')
        if isinstance(amount, (int, float)) and amount > 0:
            # Incoming: an invoice we issued that happens to share the hash.
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}

        state = (res.get('status') or '').lower()
        if state:
            status = {
                'success': PaymentStatus.SETTLED,
                'pending': PaymentStatus.IN_FLIGHT,
                'failed': PaymentStatus.FAILED,
            }.get(state, PaymentStatus.UNKNOWN)
        else:
            # Older LNbits answers with the paid/pending booleans instead.
            if res.get('paid'):
                status = PaymentStatus.SETTLED
            elif res.get('pending'):
                status = PaymentStatus.IN_FLIGHT
            else:
                status = PaymentStatus.UNKNOWN

        preimage = res.get('preimage') or None
        if preimage and not preimage.strip('0'):
            preimage = None
        if status == PaymentStatus.SETTLED and not preimage:
            # Every funding source we support returns one. One that does not
            # cannot settle a swap, so say which layer is at fault.
            logger.error(
                f"[lnbits] payment {payment_hash} settled without a preimage: "
                f"this LNbits instance's funding source does not return them, "
                f"and swaps cannot be redeemed without one"
            )
        return {'status': status, 'preimage': preimage}

    async def _probe_node_pubkey(self):
        """Read our funding-node pubkey from a dummy invoice's payee."""
        if self._node_pubkey:
            return self._node_pubkey
        try:
            invoice = await self.create_invoice(1, 'satkas_pubkey_probe')
            decoded = await self.decode_invoice(invoice) if invoice else None
            payee = (decoded or {}).get('payee')
            if payee:
                self._node_pubkey = str(payee)
                logger.info(f"lnbits funding node {self._node_pubkey[:8]}...{self._node_pubkey[-8:]}")
            else:
                logger.warning('lnbits pubkey probe: decode had no payee')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"lnbits pubkey probe failed: {e}")
        return self._node_pubkey

    async def get_node_info(self):
        pubkey = self._node_pubkey or await self._probe_node_pubkey()
        if not pubkey:
            return None
        return {'identity_pubkey': pubkey}

    async def estimate_route_fee(self, invoice=None, sat_amount=None, destination=None):
        """Returns routing fee in sats. No LNbits estimate API: max(2, 1% of amount)."""
        amt = int(sat_amount) if sat_amount else 0
        if amt <= 0 and invoice:
            decoded = await self.decode_invoice(invoice)
            msat = (decoded or {}).get('amount_msat') or 0
            amt = int(msat) // 1000
        if amt <= 0:
            return 0
        return max(2, math.ceil(0.01 * amt))


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
