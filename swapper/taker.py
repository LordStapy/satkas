
import os
import time
import json
import aiohttp
import logging

from inputimeout import inputimeout, TimeoutOccurred
from aiohttp_socks import ProxyConnector
# from dotenv import load_dotenv

from db.models import TakerWallet
from .counterparty import Counterparty
from .atomic_swap import AtomicSwap

# load_dotenv()

logger = logging.getLogger('taker')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)-8s - %(name)-16s - %(message)s'
)


class Taker(Counterparty):
    def __init__(self, output_address=None, wallet_index=1):
        super().__init__(wallet_db_table=TakerWallet, keep_unlocked=True, wallet_index=wallet_index)
        self.start_time = 0
        self.maker_endpoint = ''
        if output_address is None:
            self.output_address = self.address
        else:
            self.output_address = output_address
        self.swap = None

    async def sat2kas(self, kas_amount=1, p2p_price=None, price=None):
        # sat -> kas taker routine
        self.start_time = time.time()
        # ping maker for price
        if price is None:
            price = await self.query_price('sat2kas', kas_amount=kas_amount, p2p_price=p2p_price)
        # ping maker with receiver address and kas amount
        logger.info(f"Requesting sat2kas swap with receiver address {self.address}")
        init_swap_payload = {'receiver_address': self.address, 'kas_amount': kas_amount, 'price': price}
        init_swap_response = await self.ping_maker('init_swap', init_swap_payload)
        # receive response (swap accepted) with ln-invoice, P2SH address and sender address
        if init_swap_response['error']:
            logger.error(f"Error getting swap details from maker: {init_swap_response['error']}")
            return

        init_swap_response_payload = init_swap_response['payload']
        ln_invoice = init_swap_response_payload['ln_invoice']
        sender_address = init_swap_response_payload['sender_address']
        maker_p2sh_address = init_swap_response_payload['p2sh_address']
        maker_short_pubkey = f"{init_swap_response['pubkey'][:3]}...{init_swap_response['pubkey'][-3:]}"
        logger.info(f"Swap accepted by maker {maker_short_pubkey} with sender address {sender_address} "
                    f"and invoice {ln_invoice}")

        self.swap = AtomicSwap(
            ln_rpc_server=os.getenv('LN_RPC_SERVER', None),
            kas_rpc_server=os.getenv('KAS_RPC_SERVER', None),
            invoice=ln_invoice,
            sender_address=sender_address,
            sender_private_key=None,
            receiver_address=self.address,
            receiver_private_key=None,
            output_address=self.output_address
        )

        # generate contract address and verify it matched the address given by maker
        self.swap.decode_ln_invoice()
        assert self.swap.sat_amount == int(kas_amount * price)
        self.swap.gen_contract_address()
        if self.swap.contract_address != maker_p2sh_address:
            logger.error(f"Error, provided p2sh ({maker_p2sh_address}) differs "
                         f"from the one we generated ({self.swap.contract_address})")
            return
        # await funding of P2SH address
        utxo_sum = self.swap.check_utxo(min_amount=kas_amount+0.001)
        if not utxo_sum:
            return

        # REDEEM PATH:
        # pay the invoice, retrieving the preimage
        try:
            secret = await self.lncli_pay(self.swap.invoice, lncli=os.getenv('LNCLI', None))
            if not secret:
                raise Exception
        except Exception as e:
            logger.error(e, exc_info=True)
            timeout = int((self.swap.timelock / 1000) - time.time())
            logger.info(f"Pay the invoice then paste the preimage of the payment\n\n{self.swap.invoice}\n")
            try:
                secret = inputimeout('Insert the preimage: ', timeout=timeout).strip()
            except TimeoutOccurred:
                logger.error(f"Timeout: the invoice is expired, aborting swap")
                return

        secret_bytes = bytes.fromhex(secret)
        # set private key
        self.swap.receiver_private_key = self.get_secret_key()
        # redeem the P2SH utxo with the preimage
        swap_result = self.swap.spend_contract(secret=secret_bytes)
        if swap_result:
            logger.info(f"Redeem transaction broadcasted, txid: {swap_result}")
        self.update_address_counter()
        self.swap = None
        logger.info(f"Swap completed in {time.time() - self.start_time:.2f} seconds")
        return swap_result
        # REFUND PATH:
        # invoice is not paid, maker refund after locktime expires
        # nothing to do here, we simply avoid paying the invoice

    async def kas2sat(self, kas_amount=1, p2p_price=None, price=None):
        # kas -> sat taker routine
        self.start_time = time.time()
        # ping maker for price
        if price is None:
            price = await self.query_price('kas2sat', kas_amount=kas_amount, p2p_price=p2p_price)
        # ping maker with ln-invoice and sender address
        sat_amount = int(kas_amount * price)
        if os.getenv('LNCLI', None):
            ln_invoice = await self.gen_ln_invoice(sat_amount, lncli=os.getenv('LNCLI', None))
        else:
            ln_invoice = input(f"Generate a LN invoice for {sat_amount} sats and paste it here: ").strip()

        logger.info(f"Requesting kas2sat swap with sender address {self.address} and invoice {ln_invoice}")
        init_swap_payload = {'sender_address': self.address, 'ln_invoice': ln_invoice, 'price': price}
        init_swap_response = await self.ping_maker('init_swap', init_swap_payload)
        # receive response with receiver address, p2sh address and effective kas amount
        if init_swap_response['error']:
            logger.error(f"Error getting swap details from maker: {init_swap_response['error']}")
            return False

        init_swap_response_payload = init_swap_response['payload']
        receiver_address = init_swap_response_payload['receiver_address']
        maker_kas_amount = init_swap_response_payload['kas_amount']
        if maker_kas_amount > kas_amount:
            logger.error(f"KAS amount mismatch, our: {kas_amount}, maker: {maker_kas_amount}")
            return False
        maker_p2sh_address = init_swap_response_payload['p2sh_address']
        maker_short_pubkey = f"{init_swap_response['pubkey'][:3]}...{init_swap_response['pubkey'][-3:]}"
        logger.info(f"Swap accepted by maker {maker_short_pubkey} with receiver address {receiver_address}")

        self.swap = AtomicSwap(
            ln_rpc_server=os.getenv('LN_RPC_SERVER', None),
            kas_rpc_server=os.getenv('KAS_RPC_SERVER', None),
            invoice=ln_invoice,
            sender_address=self.address,
            sender_private_key=None,
            receiver_address=receiver_address,
            receiver_private_key=None,
            output_address=self.output_address
        )

        # generate contract address and verify it matched the address given by maker
        self.swap.decode_ln_invoice()
        self.swap.gen_contract_address()
        if self.swap.contract_address != maker_p2sh_address:
            logger.error(f"Error, provided p2sh ({maker_p2sh_address}) differs "
                         f"from the one we generated ({self.swap.contract_address})")
            return False
        # fund the P2SH address
        try:
            await self.fund_contract_address(self.swap.contract_address, amount=kas_amount)
        except Exception as e:
            logger.error(e, exc_info=True)
            logger.info(f"Pay to {self.swap.contract_address} a minimum of {maker_kas_amount + 0.001} KAS")
        utxo_sum = self.swap.check_utxo(min_amount=maker_kas_amount+0.001)
        if not utxo_sum:
            return False
        else:
            swap_ongoing = True
        swap_result = None
        while swap_ongoing:
            # REDEEM PATH:
            # invoice is paid and maker redeems the P2SH utxo
            # check invoice paid if lncli of redeem tx
            utxo_sum = await self.swap.async_check_utxo(timeout=False)
            if not utxo_sum:
                swap_ongoing = False
                logger.info(f"Maker redeemed the contract, exiting")
                swap_result = True
            # REFUND PATH:
            # invoice is not paid and taker refunds after locktime expires
            if time.time() * 1000 > self.swap.timelock + 180000 and utxo_sum:
                self.swap.sender_private_key = self.get_secret_key()
                swap_result = self.swap.spend_contract()
                if swap_result:
                    logger.info(f"Refund transaction broadcasted, txid: {swap_result}")
                    swap_ongoing = False
        self.update_address_counter()
        self.swap = None
        logger.info(f"Swap completed in {time.time() - self.start_time:.2f} seconds")
        return swap_result

    async def query_price(self, swap_type, kas_amount=0, p2p_price=None, endpoint=None):
        if endpoint is None:
            endpoint = self.maker_endpoint
        price_response = await self.ping_maker('price', {'swap_type': swap_type, 'p2p_price': p2p_price}, endpoint)
        if not price_response['error']:
            valid_until = price_response['payload']['valid_until']
            if price_response['payload'].get('price'):
                price = price_response['payload']['price']
                res = price
            elif price_response['payload'].get('offers'):
                offers = price_response['payload']['offers']
                # we don't trust the maker, so we sort the offers and select lowest price
                # offer format is int_price: (float_price, min_amt, max_amt)
                best_offer_key = sorted(offers,
                                        key=lambda x: offers[x][0],
                                        reverse=(True if swap_type == 'kas2sat' else False)
                                        )[0]
                best_offer = offers[best_offer_key]
                price = best_offer[0]
                if kas_amount and not (best_offer[1] <= kas_amount <= best_offer[2]):
                    return False
                if p2p_price is None:
                    res = offers
                else:
                    res = price
            else:
                # we should not hit this
                return False
            logger.info(f"Maker price is {price}")
        else:
            logger.error(f"Error getting quote from maker: {price_response['error']}")
            return False

        return res, valid_until

    async def ping_maker(self, msg_type, payload, endpoint=None):
        if endpoint is None:
            endpoint = self.maker_endpoint
        # msg_type is a string
        # payload is a dict
        signature = self.sign_message(msg_type, payload, node_key=True)
        req_msg = {
            'type': msg_type,
            'payload': payload,
            'pubkey': self.node_pubkey.hex(),
            'signature': signature.hex()
        }
        logger.debug(req_msg)
        connector = ProxyConnector.from_url('socks5://127.0.0.1:9050', rdns=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            if not endpoint.startswith('http') and endpoint.endswith('onion'):
                endpoint = f"http://{endpoint}"
            async with session.post(endpoint, data=json.dumps(req_msg).encode()) as res:
                response = await res.text()
                logger.debug(f"Got response: {response}")
                try:
                    data = json.loads(response)
                except Exception as e:
                    logger.error(e, exc_info=True)
                    # data = {'error': 'something went wrong'}
                if not self.verify_signature(data):
                    data['error'] = 'Signature verification failed'
                return data


async def main():
    taker = Taker(output_address='kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q')
    await taker.sat2kas(1)
    # await taker.kas2sat(1)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
