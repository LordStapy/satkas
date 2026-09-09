import asyncio
import logging
import os
from typing import List, Optional

from satkas.core.services.base_service import BaseService
from satkas.core.blib.brpc import (
    BrpcError,
    raise_for_error,
    getblockcount,
    listwallets,
    createwallet,
    loadwallet,
    getwalletinfo,
    walletpassphrase,
    getbalance,
    getnewaddress,
    listunspent,
    sendtoaddress,
)

logger = logging.getLogger('bitcoind_wallet_service')


class BitcoindWalletService(BaseService):
    """
    Bitcoin spending wallet via bitcoind RPC (wallet with private keys).
    Separate from BitcoindService watch-only monitor wallet.
    Silently unlocks encrypted wallets before send.
    """
    service_icon = "wallet"
    icon_style = "bitcoin"

    default_host = '127.0.0.1'
    default_port = 8332
    default_wallet_name = 'satkas_wallet'
    default_unlock_timeout = 60

    def __init__(self, host=None, port=None):
        super().__init__()
        self.service_name = 'Bitcoind Wallet'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        self.configs = {
            'host': {
                'attr': 'host',
                'default': self.default_host,
                'fallback': None,
                'type': str,
                'env': 'BITCOIND_HOST',
            },
            'port': {
                'attr': 'port',
                'default': self.default_port,
                'fallback': None,
                'type': int,
                'env': 'BITCOIND_PORT',
            },
            'rpc_user': {
                'attr': 'rpc_user',
                'default': '',
                'fallback': None,
                'type': str,
                'env': 'BTC_RPC_USER',
            },
            'rpc_pass': {
                'attr': 'rpc_pass',
                'default': '',
                'fallback': None,
                'type': str,
                'env': 'BTC_RPC_PASS',
            },
            'wallet_name': {
                'attr': 'wallet_name',
                'default': self.default_wallet_name,
                'fallback': None,
                'type': str,
                'env': 'BTC_SPENDING_WALLET_NAME',
            },
            'wallet_passphrase': {
                'attr': 'wallet_passphrase',
                'default': '',
                'fallback': None,
                'type': str,
                'env': 'BTC_WALLET_PASSPHRASE',
            },
            'unlock_timeout': {
                'attr': 'unlock_timeout',
                'default': self.default_unlock_timeout,
                'fallback': None,
                'type': int,
                'env': 'BTC_WALLET_UNLOCK_TIMEOUT',
            },
        }

        if host is None and port is None:
            self.load_config()
        else:
            self.host = host if host else self.default_host
            self.port = port if port else self.default_port
            self.rpc_user = os.getenv('BTC_RPC_USER', '')
            self.rpc_pass = os.getenv('BTC_RPC_PASS', '')
            self.wallet_name = os.getenv('BTC_SPENDING_WALLET_NAME', self.default_wallet_name)
            self.wallet_passphrase = os.getenv('BTC_WALLET_PASSPHRASE', '')
            self.unlock_timeout = int(
                os.getenv('BTC_WALLET_UNLOCK_TIMEOUT', self.default_unlock_timeout)
            )

        if not hasattr(self, 'host') or self.host is None:
            self.host = self.default_host
        if not hasattr(self, 'port') or self.port is None:
            self.port = self.default_port
        if not hasattr(self, 'rpc_user') or self.rpc_user is None:
            self.rpc_user = ''
        if not hasattr(self, 'rpc_pass') or self.rpc_pass is None:
            self.rpc_pass = ''
        if not hasattr(self, 'wallet_name') or self.wallet_name is None:
            self.wallet_name = self.default_wallet_name
        if not hasattr(self, 'wallet_passphrase') or self.wallet_passphrase is None:
            self.wallet_passphrase = ''
        if not hasattr(self, 'unlock_timeout') or self.unlock_timeout is None:
            self.unlock_timeout = self.default_unlock_timeout

        self._wallet_ready = False
        self.update_status_task = None
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text):
        if not text:
            self.host = self.default_host
            self.port = self.default_port
            return
        parts = text.split(':')
        if len(parts) >= 2:
            self.host = parts[0]
            self.port = int(parts[1])
            if len(parts) >= 3 and parts[2]:
                self.wallet_name = parts[2]
        else:
            self.host = text
            self.port = self.default_port

    @property
    def config_string(self):
        return f"{self.host}:{self.port}"

    def _rpc_kwargs(self, with_wallet=False):
        kwargs = {
            'server': f"{self.host}:{self.port}",
            'user': self.rpc_user,
            'password': self.rpc_pass,
        }
        if with_wallet:
            kwargs['rpcwallet'] = self.wallet_name
        return kwargs

    # --- Lifecycle ---

    async def detect(self):
        try:
            res = await getblockcount(**self._rpc_kwargs())
            raise_for_error(res)
            try:
                await listwallets(**self._rpc_kwargs())
            except BrpcError as e:
                logger.error(f"bitcoind wallet RPC unavailable: {e}")
                self.is_detected = False
                self.service_status_string = 'Wallet support missing'
                self._notify_change(['is_detected', 'service_status_string'])
                return False
            self.is_detected = True
        except Exception as e:
            logger.debug(f"BitcoindWallet detect failed: {e}")
            self.is_detected = False
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        if not self.is_detected:
            return False
        try:
            await self._ensure_spending_wallet()
            return self._wallet_ready
        except Exception as e:
            logger.error(f"BitcoindWallet validate failed: {e}")
            return False

    def enable(self):
        self.is_enabled = True
        os.environ['BITCOIND_HOST'] = str(self.host)
        os.environ['BITCOIND_PORT'] = str(self.port)
        os.environ['BTC_RPC_USER'] = self.rpc_user or ''
        os.environ['BTC_RPC_PASS'] = self.rpc_pass or ''
        os.environ['BTC_SPENDING_WALLET_NAME'] = self.wallet_name
        self.update_status_task = asyncio.create_task(self.update_status(run_once=False))
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            self.update_status_task.cancel()
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once=True):
        while True:
            if self.is_detected and self.is_enabled:
                try:
                    await self._ensure_spending_wallet()
                    balance_sats = await self.get_balance()
                    locked = await self._is_locked()
                    lock_str = 'locked' if locked else 'unlocked'
                    status_string = (
                        f"Server: {self.config_string}\n"
                        f"Wallet: {self.wallet_name} ({lock_str})\n"
                        f"Balance: {balance_sats} sats"
                    )
                except Exception:
                    status_string = 'Error getting wallet info'
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

    # --- Wallet internals ---

    async def _ensure_spending_wallet(self):
        if self._wallet_ready:
            return
        wallets_res = await listwallets(**self._rpc_kwargs())
        raise_for_error(wallets_res)
        loaded = wallets_res.get('result') or []
        if self.wallet_name in loaded:
            self._wallet_ready = True
            return

        load_res = await loadwallet(self.wallet_name, **self._rpc_kwargs())
        if load_res.get('error') is None:
            self._wallet_ready = True
            logger.info(f"Loaded spending wallet: {self.wallet_name}")
            return

        # Create descriptor wallet with private keys
        create_res = await createwallet(
            self.wallet_name,
            disable_private_keys=False,
            descriptors=True,
            passphrase=self.wallet_passphrase or '',
            **self._rpc_kwargs(),
        )
        err = create_res.get('error')
        if err is not None:
            load_res = await loadwallet(self.wallet_name, **self._rpc_kwargs())
            raise_for_error(load_res)
        self._wallet_ready = True
        logger.info(f"Spending wallet ready: {self.wallet_name}")

    async def _is_locked(self) -> bool:
        """True if wallet is encrypted and currently locked."""
        try:
            res = await getwalletinfo(**self._rpc_kwargs(with_wallet=True))
            raise_for_error(res)
            info = res.get('result') or {}
            # unlocked_until present only on encrypted wallets; 0 means locked
            if 'unlocked_until' not in info:
                return False
            return int(info.get('unlocked_until', 0) or 0) == 0
        except BrpcError:
            return False

    async def _ensure_unlocked(self):
        await self._ensure_spending_wallet()
        if not await self._is_locked():
            return
        if not self.wallet_passphrase:
            raise BrpcError({
                'message': f'Wallet {self.wallet_name} is locked but no passphrase configured'
            })
        res = await walletpassphrase(
            self.wallet_passphrase,
            self.unlock_timeout,
            **self._rpc_kwargs(with_wallet=True),
        )
        raise_for_error(res)
        logger.debug(f"Unlocked wallet {self.wallet_name} for {self.unlock_timeout}s")

    # --- Public API ---

    async def get_address(self, *, address_type: str = 'bech32') -> str:
        await self._ensure_spending_wallet()
        res = await getnewaddress(
            '',
            address_type,
            **self._rpc_kwargs(with_wallet=True),
        )
        raise_for_error(res)
        return res['result']

    async def get_new_address(self, *, address_type: str = 'bech32') -> str:
        return await self.get_address(address_type=address_type)

    async def get_balance(self) -> int:
        """Return trusted balance in sats."""
        await self._ensure_spending_wallet()
        res = await getbalance(**self._rpc_kwargs(with_wallet=True))
        raise_for_error(res)
        btc = float(res.get('result') or 0)
        return int(round(btc * 1e8))

    async def list_unspent(
        self,
        addresses: Optional[List[str]] = None,
        minconf: int = 0,
    ) -> list:
        await self._ensure_spending_wallet()
        res = await listunspent(
            addresses if addresses is not None else [],
            minconf=minconf,
            **self._rpc_kwargs(with_wallet=True),
        )
        raise_for_error(res)
        return res.get('result') or []

    async def send_to_address(self, address: str, amount_sats: int, **kwargs) -> str:
        """
        Send amount_sats to address. Silently unlocks if needed.
        Returns txid.
        """
        await self._ensure_unlocked()
        amount_btc = amount_sats / 1e8
        res = await sendtoaddress(
            address,
            amount_btc,
            **self._rpc_kwargs(with_wallet=True),
            **kwargs,
        )
        raise_for_error(res)
        result = res.get('result')
        # Some bitcoind versions return just the txid string
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get('txid', '')
        return str(result)
