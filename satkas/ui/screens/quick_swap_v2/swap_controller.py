"""
Swap Controller - Centralized business logic for swaps
Temporary code, until the logic is fully integrated in the swapper classes.
(waiting for on-chain swaps research to be completed before changing the swapper)
"""

import asyncio
import time
import hashlib
from typing import Optional, Dict, Any


class SwapController:
    """Handles all swap business logic - init, monitoring, payment, cancellation."""
    
    def __init__(self, taker, service_manager):
        self.taker = taker
        self.service_manager = service_manager
        self.current_swap = None
        self.monitor_task: Optional[asyncio.Task] = None
        
        # State
        self.swap_direction = "kas2sat"
        self.kas_amount = 0
        self.sat_amount = 0
        self.rate = 0
        self.contract_address = ""
        self.contract_funded = False
        self.lightning_invoice = ""
        self.is_active = False
        
    async def init_swap(self, direction: str, kas_amount: float, rate: float, **kwargs) -> Dict[str, Any]:
        """Initialize swap for either direction."""
        self.swap_direction = direction
        self.kas_amount = kas_amount
        self.rate = rate
        self.sat_amount = int(kas_amount * rate)
        
        if direction == "kas2sat":
            return await self._init_kas2sat(**kwargs)
        else:
            return await self._init_sat2kas(**kwargs)
    
    async def _init_kas2sat(self, invoice=None) -> Dict[str, Any]:
        """Initialize KAS→SAT swap."""
        # Create invoice
        sat_amount = self.sat_amount

        if invoice is None:
            invoice = await self.create_invoice(sat_amount)
        
        # Init swap with maker
        response = await self.taker.init_swap(
            sender_address=self.taker.address,
            ln_invoice=invoice,
            price=self.rate,
            kas_amount=self.kas_amount
        )
        
        self.lightning_invoice = invoice
        self.contract_address = response['p2sh_address']
        self.is_active = True
        
        return {
            'invoice': invoice,
            'contract_address': response['p2sh_address'],
            'kas_amount': response.get('kas_amount', self.kas_amount)
        }
    
    async def _init_sat2kas(self) -> Dict[str, Any]:
        """Initialize SAT→KAS swap."""
        # Init swap with maker
        response = await self.taker.init_swap(
            receiver_address=self.taker.address,
            price=self.rate,
            kas_amount=self.kas_amount
        )
        
        self.lightning_invoice = response['ln_invoice']
        self.contract_address = response['p2sh_address']
        self.is_active = True
        
        return {
            'invoice': response['ln_invoice'],
            'contract_address': response['p2sh_address'],
            'kas_amount': response.get('kas_amount', self.kas_amount)
        }
    
    async def monitor_swap(self, on_update_callback):
        """Monitor swap progress with callback for UI updates."""
        if self.swap_direction == "kas2sat":
            self.monitor_task = asyncio.create_task(
                self._monitor_kas2sat(on_update_callback)
            )
        else:
            self.monitor_task = asyncio.create_task(
                self._monitor_sat2kas(on_update_callback)
            )
    
    async def _monitor_kas2sat(self, callback):
        """Monitor KAS→SAT swap."""
        time_remaining = int(self.taker.swap.timelock / 1e3 - time.time())
        
        while time_remaining > 0:
            if not self.contract_funded:
                funded = await self.taker.swap.async_check_utxo(timeout=False)
            else:
                funded = self.kas_amount

            cb_payload = {
                'status': 'monitoring',
                'funded': funded,
                'time_remaining': time_remaining
            }

            if funded >= self.kas_amount:
                if not self.contract_funded:
                    self.contract_funded = True
                cb_payload['status'] = 'funded'
                await callback(cb_payload)
                # Check for completion (invoice paid, funds redeemed)
                if await self._check_kas2sat_complete(funded):
                    await callback({'status': 'completed'})
                    break
            else:
                await callback(cb_payload)
            await asyncio.sleep(1)
            time_remaining = int(self.taker.swap.timelock / 1e3 - time.time())
        
        if time_remaining <= 0:
            await callback({'status': 'expired'})
    
    async def _monitor_sat2kas(self, callback):
        """Monitor SAT→KAS swap."""
        awaiting_user_payment = False
        time_remaining = int(self.taker.swap.timelock / 1e3 - time.time())
        
        # Wait for maker to fund contract
        while time_remaining > 0:
            if not self.contract_funded:
                funded = await self.taker.swap.async_check_utxo(timeout=False)
            else:
                funded = self.kas_amount
            
            cb_payload = {
                'status': 'monitoring',
                'funded': funded,
                'time_remaining': time_remaining
            }

            if funded >= self.kas_amount:
                self.contract_funded = True
                # Check confirmations
                if self.taker.swap.check_daa_confirmations():
                    if await self._check_sat2kas_complete(funded):
                        # note to myself: Why did I comment this out? Is status set by other methods?
                        # (check the redeem with preimage methods if they set the status themselves)
                        # Yes, the status is set by other methods. Leaving this here for reference.
                        #await callback({'status': 'completed'})
                        break
                    elif not awaiting_user_payment:
                        awaiting_user_payment = True
                        cb_payload['status'] = 'ready_to_pay'
                        cb_payload['confirmed'] = True
                    else:
                        cb_payload['status'] = 'waiting_user_payment'
                else:
                    cb_payload['status'] = 'waiting_confirmations'
            else:
                cb_payload['status'] = 'waiting_funding'
            await callback(cb_payload)
            await asyncio.sleep(1)
            time_remaining = int(self.taker.swap.timelock / 1e3 - time.time())
        
        if time_remaining <= 0:
            await callback({'status': 'expired'})
    
    async def _check_kas2sat_complete(self, funded):
        """Check if kas2sat is complete (funds spent)."""
        # if ln wallet is not external, we check if the invoice was paid
        if self.service_manager._preferred_ln_wallet != "external":
            # check invoice status, ToDo
            pass

        # anyway, we check if the contract was funded and then redeemed 
        if self.contract_funded:
            return await self.taker.swap.async_check_utxo(timeout=False) < self.kas_amount
        else:
            return False

    async def _check_sat2kas_complete(self, funded):
        """Check if sat2kas is complete (invoice paid and funds redeemed)."""
        # we skip the invoice check for now
        
        if self.contract_funded:
            return await self.taker.swap.async_check_utxo(timeout=False) < self.kas_amount
        else:
            return False


    async def create_invoice(self, amount: int) -> str:
        """Create invoice with internal LN wallet."""
        return await self.service_manager.ln_wallet_service.create_invoice(
            amount=amount
        )
    
    async def pay_with_internal_ln_wallet(self):
        """Pay invoice with internal LN wallet and return preimage."""
        payment_result = await self.service_manager.ln_wallet_service.pay_invoice(
            self.lightning_invoice
        )
        
        # Extract preimage
        preimage = payment_result.get('payment_preimage') or payment_result.get('preimage')
        return preimage
    
    async def redeem_with_preimage(self, preimage_hex: str) -> str:
        """Redeem sat2kas contract with preimage."""
        preimage_bytes = bytes.fromhex(preimage_hex)
        
        # Validate preimage
        if not self._validate_preimage(preimage_hex):
            raise ValueError("Invalid preimage")
        
        # Set keys and redeem
        self.taker.swap.receiver_private_key = self.taker.get_secret_key()
        if not self.taker.swap.output_address:
            self.taker.swap.output_address = self.taker.output_address
        
        txid = self.taker.swap.spend_contract(secret=preimage_bytes)
        return txid
    
    def _validate_preimage(self, preimage_hex: str) -> bool:
        """Validate preimage matches payment hash."""
        if len(preimage_hex) != 64:
            return False
        
        preimage_bytes = bytes.fromhex(preimage_hex)
        calculated_hash = hashlib.sha256(preimage_bytes).digest()
        expected_hash = self.taker.swap.secret_hash
        
        return calculated_hash == expected_hash
    
    async def pay_with_kaspa_wallet(self):
        """Pay contract with Kaspa wallet."""
        await self.service_manager.kaspa_wallet_service.pay(
            self.contract_address,
            self.kas_amount
        )
    
    def cancel_swap(self):
        """Cancel active swap."""
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            self.monitor_task = None
        
        self.is_active = False
        self.taker.db_set_swap_status('EXPIRED')
        self.taker.swap = None
    
    def reset(self):
        """Reset controller state."""
        self.cancel_swap()
        self.current_swap = None
        self.kas_amount = 0
        self.sat_amount = 0
        self.rate = 0
        self.contract_address = ""
        self.lightning_invoice = ""
        self.swap_direction = "kas2sat"
        self.contract_funded = False
        self.is_active = False

