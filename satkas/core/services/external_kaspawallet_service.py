import asyncio
import aiohttp
import json
from typing import Optional, Dict, Any
from satkas.core.services.base_service import BaseService, ExternalWalletRequired


class ExternalKaspaWalletService(BaseService):
    """
    External Kaspa Wallet Service that prompts the user to use external wallet to send and receive transactions.
    """
    can_refresh = False
    service_icon = "wallet-outline"
    icon_style = "kaspa"

    def __init__(self):
        super().__init__()
        self.service_name = "External Kaspa Wallet"
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
        # no need to update status for external wallet
        asyncio.create_task(self.update_status(run_once=True))
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            self.update_status_task.cancel()
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once: bool = True):
        """Update status of external Kaspa wallet"""
        while True:
            if self.is_detected and self.is_enabled:
                # External wallet - status is always ready for user interaction
                status_string = "Ready for external Kaspa wallet"
                if status_string != self.service_status_string:
                    self.service_status_string = status_string
                    self._notify_change(['service_status_string'])
            if run_once:
                break
            await asyncio.sleep(30)  # Update less frequently for external service
    
    async def get_address(self) -> Optional[str]:
        """Get wallet address from external service"""
        raise ExternalWalletRequired("Provide a receiving address from your Kaspa wallet")

    async def create_transaction(self, to_address: str, amount: float, fee: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Create a transaction"""
        raise ExternalWalletRequired(
            f"Create a transaction of {amount} KAS to {to_address} with your Kaspa wallet"
        )

    async def send_transaction(self, to_address: str, amount: float, fee: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Send a transaction"""
        raise ExternalWalletRequired(
            f"Send {amount} KAS to {to_address} with your Kaspa wallet"
        )

    async def pay(self, destination, amount, sm=None):
        """Alias for send_transaction to match go kaspawallet_service.pay"""
        return await self.send_transaction(destination, amount)


# Example usage and testing
async def test_external_wallet():
    """Test the external wallet service"""
    print("Testing ExternalKaspaWalletService structure...")

    # Create service instance
    wallet = ExternalKaspaWalletService()

    print(f"Service name: {wallet.service_name}")
    print("External wallet service structure created successfully!")


if __name__ == '__main__':
    asyncio.run(test_external_wallet())
