"""Progress events emitted while a swap runs.

The core emits SwapEvent and leaves rendering to whoever is listening, so the
same swap flow can drive the Kivy screen, a CLI run, or a web frontend.

as_dict() reproduces the payload shape the quick_swap_v2 controller already
passes to its callback, so the UI can keep consuming dicts until it migrates
to the dataclass.
"""

from dataclasses import dataclass, fields
from typing import Optional


class SwapStatus:
    """Status values the quick_swap_v2 screen currently reacts to.

    COMPLETING/REFUNDING are emitted at broadcast by the orchestrators; the
    screen shows a mid-settlement state until COMPLETED/REFUNDED arrives after
    confirmation promote.
    """

    MONITORING = 'monitoring'
    WAITING_FUNDING = 'waiting_funding'
    FUNDED = 'funded'
    WAITING_CONFIRMATIONS = 'waiting_confirmations'
    READY_TO_PAY = 'ready_to_pay'
    WAITING_USER_PAYMENT = 'waiting_user_payment'
    WAITING_BTC_FUNDING = 'waiting_btc_funding'
    BTC_FUNDED = 'btc_funded'
    WAITING_COUNTERPARTY = 'waiting_counterparty'
    READY_TO_REDEEM = 'ready_to_redeem'
    COMPLETING = 'completing'
    COMPLETED = 'completed'
    EXPIRED = 'expired'

    SWAP_INITIALIZED = 'swap_initialized'
    AWAITING_MANUAL_FUNDING = 'awaiting_manual_funding'
    REFUNDING = 'refunding'
    REFUNDED = 'refunded'
    FAILED = 'failed'


@dataclass
class SwapEvent:
    status: str
    funded: Optional[float] = None
    confirmed: Optional[bool] = None
    btc_funded: Optional[bool] = None
    kas_amount: Optional[float] = None
    sat_amount: Optional[int] = None
    time_remaining: Optional[int] = None
    blocks_remaining: Optional[int] = None
    daa_remaining: Optional[int] = None
    contract_address: Optional[str] = None
    btc_contract_address: Optional[str] = None
    invoice: Optional[str] = None
    txid: Optional[str] = None
    error: Optional[str] = None

    def as_dict(self):
        """Unset fields are omitted, so a listener's get(key, default) still
        falls back to its own default rather than reading a None."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }
