import asyncio
from typing import List, Optional

from satkas.core.services.base_service import BaseService, ExternalWalletRequired


class ExternalBtcWalletService(BaseService):
    """
    External Bitcoin wallet service — prompts the user to send/receive with an
    external wallet. Method signatures match BitcoindWalletService.
    """
    can_refresh = False
    service_icon = "wallet-outline"
    icon_style = "bitcoin"

    def __init__(self):
        super().__init__()
        self.service_name = 'External BTC Wallet'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        self.update_status_task = None
        self.configs = {}
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text: str):
        pass

    @property
    def config_string(self) -> str:
        return ''

    async def detect(self) -> bool:
        self.is_detected = True
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self) -> bool:
        return True

    def enable(self):
        self.is_enabled = True
        asyncio.create_task(self.update_status(run_once=True))
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            self.update_status_task.cancel()
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once: bool = True):
        while True:
            if self.is_detected and self.is_enabled:
                status_string = 'Ready for external BTC wallet'
                if status_string != self.service_status_string:
                    self.service_status_string = status_string
                    self._notify_change(['service_status_string'])
            if run_once:
                break
            await asyncio.sleep(30)

    # --- Public API (same signatures as BitcoindWalletService) ---

    async def get_address(self, *, address_type: str = 'bech32') -> str:
        raise NotImplementedError('get_address not implemented for external BTC wallet')

    async def get_balance(self) -> int:
        raise NotImplementedError('get_balance not implemented for external BTC wallet')

    async def list_unspent(
        self,
        addresses: Optional[List[str]] = None,
        minconf: int = 0,
    ) -> list:
        raise NotImplementedError('list_unspent not implemented for external BTC wallet')

    async def send_to_address(self, address: str, amount_sats: int, **kwargs) -> str:
        raise ExternalWalletRequired(
            f"Send {amount_sats} sats to {address} with your BTC wallet"
        )
