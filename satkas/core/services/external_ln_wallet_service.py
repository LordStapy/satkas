import asyncio
import math
from typing import Optional, Dict, Any

from bolt11.decode import decode as bolt11_decode
from satkas.core.services.base_service import BaseService, ExternalWalletRequired


class ExternalLNWalletService(BaseService):
    """
    External Lightning Network Wallet Service that prompts the user to use external wallet
    to send and receive lightning payments.
    """
    can_refresh = False
    service_icon = "lightning-bolt-outline"
    icon_style = "lightning"

    def __init__(self):
        super().__init__()
        self.service_name = "External LN Wallet"
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        self.update_status_task = None

        self.configs = {}

        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text: str):
        """Parse configuration string - not used for external wallet"""
        pass

    @property
    def config_string(self) -> str:
        """Return configuration string - empty for external wallet"""
        return ''

    async def detect(self) -> bool:
        """External wallet is always available"""
        self.is_detected = True
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self) -> bool:
        return True

    def enable(self):
        """Enable the external LN wallet service"""
        self.is_enabled = True
        # no need to update status for external wallet
        asyncio.create_task(self.update_status(run_once=True))
        self._notify_change(['is_enabled'])

    def disable(self):
        """Disable the external LN wallet service"""
        self.is_enabled = False
        # if self.update_status_task:
        #     self.update_status_task.cancel()
        #     self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once: bool = True):
        """Update status of external LN wallet"""
        while True:
            if self.is_detected and self.is_enabled:
                # External wallet - status is always ready for user interaction
                status_string = "Ready for external LN payments"
                if status_string != self.service_status_string:
                    self.service_status_string = status_string
                    self._notify_change(['service_status_string'])
            if run_once:
                break
            await asyncio.sleep(30)  # Update less frequently for external service
            

    async def get_wallet(self) -> Optional[Dict[str, Any]]:
        """Get wallet information from external service"""
        raise NotImplementedError("get_wallet method not yet implemented")

    async def get_balance(self) -> Optional[int]:
        """Get wallet balance in sats from external service"""
        raise NotImplementedError("get_balance method not yet implemented")

    async def create_invoice(self, amount: int = 0, memo: str = '') -> Optional[Dict[str, Any]]:
        """Create a lightning invoice for receiving payment"""
        # this will require user to input the payment invoice manually
        raise ExternalWalletRequired(
            f"Create an invoice for {amount} sats with your LN wallet and paste it here"
        )

    async def decode_invoice(self, invoice):
        decoded = bolt11_decode(invoice)
        # date = decoded.date
        # expiry = decoded.expiry
        # secret_hash = decoded.payment_hash
        # expiry_ts = (int(date) + (int(expiry)))
        # expiry_ts_max = int(time.time()) + int(os.getenv('MAX_LN_INVOICE_EXPIRY', 3600))
        # if expiry_ts > expiry_ts_max:
        #     raise ValueError('Invoice expiry time is too big.')
        print(f"[external_ln_wallet] Decoded invoice: {decoded}")
        # craft json response with the decoded invoice
        decoded = {
            'amount_msat': decoded.amount_msat,
            'date': decoded.date,
            'expiry': decoded.expiry,
            'payment_hash': decoded.payment_hash,
            'description': decoded.description,
            'payee': decoded.payee,
        }
        return decoded

    async def validate_invoice(self, invoice, parsed_data=None):
        """Validate a lightning invoice"""
        if parsed_data is None:
            parsed_data = await self.decode_invoice(invoice)
        # TODO: invoice validation, we return True for now
        # list of checks to do: 
        # 1. expiration time must be in the future, but not too far in the future (based on settings' max expiry time)
        # 2. payee (destination) must be different than the source, unless we are validating an invoice created by us
        return True

    async def pay_invoice(self, invoice: str) -> Optional[Dict[str, Any]]:
        """Pay a lightning invoice"""
        # this will require user to pay the invoice externally and then enter the payment preimage here
        raise ExternalWalletRequired(
            "Pay the invoice with your LN wallet and paste the payment preimage here"
        )

    async def check_invoice(self, payment_hash: str) -> Optional[Dict[str, Any]]:
        """Check status of a lightning payment/invoice"""
        raise ExternalWalletRequired(
            "Check the payment status in your LN wallet"
        )

    async def check_payment(self, payment_hash: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
        """Status of an outgoing payment.

        Raises rather than returning UNKNOWN so that this service keeps the
        same signature and the same failure mode as its siblings. The caller
        catches it and reads it as UNKNOWN, which is the cautious answer
        anyway: an external wallet may well have paid without telling us.
        """
        raise ExternalWalletRequired(
            "Check the payment status in your LN wallet"
        )

    async def get_node_info(self) -> Optional[Dict[str, Any]]:
        """Get lightning node information (pubkey, alias, etc.)"""
        raise NotImplementedError("get_node_info method not yet implemented")

    async def estimate_route_fee(self, invoice=None, sat_amount=None, destination=None):
        """Returns routing fee in sats. Same reserve as LNbits: max(2, 1% of amount)."""
        amt = int(sat_amount) if sat_amount else 0
        if amt <= 0 and invoice:
            decoded = bolt11_decode(invoice)
            msat = decoded.amount_msat or 0
            amt = int(msat) // 1000
        if amt <= 0:
            return 0
        return max(2, math.ceil(0.01 * amt))


# Example usage and testing
async def test_external_ln_wallet():
    """Test the external LN wallet service"""
    print("Testing ExternalLNWalletService structure...")

    # Create service instance
    wallet = ExternalLNWalletService()

    print(f"Service name: {wallet.service_name}")
    print("External LN wallet service structure created successfully!")


if __name__ == '__main__':
    asyncio.run(test_external_ln_wallet())
