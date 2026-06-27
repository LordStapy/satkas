import datetime
import os
import ssl
import time
import json
import aiohttp
import logging
import certifi

from inputimeout import inputimeout, TimeoutOccurred
from aiohttp_socks import ProxyConnector

from satkas.core.db.models import TakerWallet, Swap
from satkas.core.swapper.counterparty import Counterparty
from satkas.core.swapper.atomic_swap import AtomicSwap


logger = logging.getLogger('taker')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)-8s - %(name)-16s - %(message)s'
)


class Taker(Counterparty):
    def __init__(self, output_address=None, wallet_index=1, wallet_passwd=None):
        super().__init__(
            wallet_db_table=TakerWallet,
            keep_unlocked=True,
            wallet_index=wallet_index,
            wallet_passwd=wallet_passwd
        )
        self.start_time = 0
        self.maker_endpoint = ''
        if output_address is None:
            self.output_address = self.address
        else:
            self.output_address = output_address
        self.swap = None
        self.db_swap = None

    async def init_swap(self, **kwargs):
        if kwargs.get('receiver_address'):
            swap_type = 'sat2kas'
        else:
            swap_type = 'kas2sat'
        init_swap_payload = kwargs
        init_swap_response = await self.ping_maker('init_swap', init_swap_payload)
        if init_swap_response['error']:
            logger.error(f"Error getting swap details from maker: {init_swap_response['error']}")
            return
        init_swap_response_payload = init_swap_response['payload']
        if swap_type == 'sat2kas':
            sender_address = init_swap_response_payload['sender_address']
            receiver_address = self.address
            ln_invoice = init_swap_response_payload['ln_invoice']
            kas_amount = kwargs.get('kas_amount')
            maker_info = f"{sender_address} and invoice {ln_invoice}"
        else:
            sender_address = self.address
            receiver_address = init_swap_response_payload['receiver_address']
            ln_invoice = kwargs['ln_invoice']
            kas_amount = init_swap_response_payload['kas_amount']
            maker_info = f"{receiver_address}"
        maker_p2sh_address = init_swap_response_payload['p2sh_address']
        maker_short_pubkey = f"{init_swap_response['pubkey'][:3]}...{init_swap_response['pubkey'][-3:]}"
        logger.info(f"Swap {swap_type} accepted by maker {maker_short_pubkey} with address {maker_info}")
        swap = AtomicSwap(
            ln_rpc_server=os.getenv('LN_RPC_SERVER', None),
            kas_rpc_server=os.getenv('KAS_RPC_SERVER', None),
            invoice=ln_invoice,
            sender_address=sender_address,
            sender_private_key=None,
            receiver_address=receiver_address,
            receiver_private_key=None,
            output_address=self.output_address
        )
        try:
            swap.decode_ln_invoice()
        except ValueError as e:
            logger.error(e)
            return False
        swap.gen_contract_address()
        if swap.contract_address != maker_p2sh_address:
            logger.error(f"Error, provided p2sh ({maker_p2sh_address}) differs "
                         f"from the one we generated ({swap.contract_address})")
            return False
        self.swap = swap
        self.db_swap = Swap.create(
            swap_type=swap_type,
            side='taker',
            remote_pubkey=init_swap_response['pubkey'],
            ln_invoice=ln_invoice,
            payment_hash=self.swap.secret_hash.hex(),
            sender_address=sender_address,
            receiver_address=receiver_address,
            contract=self.swap.contract_script.hex(),
            p2sh_address=self.swap.contract_address,
            dwork_amount=int(kas_amount * 1e8),
            status='INIT',  # INIT / PENDING / COMPLETED / REFUNDED / EXPIRED
        )
        return init_swap_response_payload

    async def sat2kas(self, kas_amount=1, p2p_price=None, price=None):
        # sat -> kas taker routine
        self.start_time = time.time()
        # ping maker for price
        if price is None:
            price = await self.query_price('sat2kas', kas_amount=kas_amount, p2p_price=p2p_price)
        # ping maker with receiver address and kas amount
        logger.info(f"Requesting sat2kas swap with receiver address {self.address}")

        init_swap_response = await self.init_swap(
            receiver_address=self.address,
            kas_amount=kas_amount,
            price=price
        )
        if not init_swap_response:
            return

        assert self.swap.sat_amount == int(kas_amount * price)

        # await funding of P2SH address
        utxo_sum = self.swap.check_utxo(min_amount=kas_amount+0.01)
        if not utxo_sum:
            self.db_set_swap_status('EXPIRED')
            return
        else:
            self.db_set_swap_status('PENDING', finalize_swap=False)

        while not self.swap.check_daa_confirmations():
            time.sleep(1)

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
                self.db_set_swap_status('EXPIRED')
                return

        secret_bytes = bytes.fromhex(secret)
        # set private key
        self.swap.receiver_private_key = self.get_secret_key()
        # redeem the P2SH utxo with the preimage
        swap_result = self.swap.spend_contract(secret=secret_bytes)
        if swap_result:
            logger.info(f"Redeem transaction broadcasted, txid: {swap_result}")
            self.db_set_swap_status('COMPLETED')
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

        init_swap_response = await self.init_swap(
            sender_address=self.address,
            ln_invoice=ln_invoice,
            price=price
        )
        if not init_swap_response:
            return

        maker_kas_amount = init_swap_response.get('kas_amount')
        if maker_kas_amount > kas_amount:
            logger.error(f"KAS amount mismatch, our: {kas_amount}, maker: {maker_kas_amount}")
            return False
        # fund the P2SH address
        try:
            await self.fund_contract_address(self.swap.contract_address, amount=kas_amount)
        except Exception as e:
            logger.error(e, exc_info=True)
            logger.info(f"Pay to {self.swap.contract_address} a minimum of {maker_kas_amount + 0.01} KAS")
        utxo_sum = self.swap.check_utxo(min_amount=maker_kas_amount+0.01)
        if not utxo_sum:
            self.db_set_swap_status('EXPIRED')
            return False
        else:
            swap_ongoing = True
            self.db_set_swap_status('PENDING', finalize_swap=False)

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
                self.db_set_swap_status('COMPLETED')
            # REFUND PATH:
            # invoice is not paid and taker refunds after locktime expires
            if time.time() * 1000 > self.swap.timelock + 180000 and utxo_sum:
                self.swap.sender_private_key = self.get_secret_key()
                swap_result = self.swap.spend_contract()
                if swap_result:
                    logger.info(f"Refund transaction broadcasted, txid: {swap_result}")
                    swap_ongoing = False
                    self.db_set_swap_status('REFUNDED')
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
                # filter valid offers if a kas amount is provided
                if kas_amount:
                    offers = {k: v for k, v in offers.items() if v[1] <= kas_amount <= v[2]}
                    # if no offers are left, return False
                    if not offers:
                        return False
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
                # we should not hit this -> (time passes) -> actually, we can hit this if the maker has no offers
                return False
            logger.debug(f"Maker price is {price}")
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
        if endpoint.endswith('onion'):
            connector = ProxyConnector.from_url('socks5://127.0.0.1:9050', rdns=True)
            if not endpoint.startswith('http'):
                endpoint = f"http://{endpoint}"
        else:
            # enforce https for clearnet endpoint
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            if not endpoint.startswith('https'):
                endpoint = f"https://{endpoint}"
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(endpoint, data=json.dumps(req_msg).encode()) as res:
                response = await res.text()
                logger.debug(f"Got response: {response}")
                try:
                    data = json.loads(response)
                except Exception as e:
                    logger.error(e, exc_info=True)
                    # data = {'error': 'something went wrong'}
                if not data.get('error') and not self.verify_signature(data):
                    data['error'] = 'Signature verification failed'
                return data

    @staticmethod
    def db_load_swaps(limit=10):
        swaps = Swap.select().order_by(Swap.created_at.desc()).limit(limit)
        return list(swaps)

    def db_load_swap(self, swap):
        if swap.status not in ['PENDING', 'INIT']:
            return False
        self.db_swap = swap
        self.swap = AtomicSwap(
            ln_rpc_server=os.getenv('LN_RPC_SERVER', None),
            kas_rpc_server=os.getenv('KAS_RPC_SERVER', None),
            invoice=swap.ln_invoice,
            sender_address=swap.sender_address,
            sender_private_key=None,
            receiver_address=swap.receiver_address,
            receiver_private_key=None,
            output_address=self.output_address
        )
        self.swap.decode_ln_invoice()
        self.swap.gen_contract_address()
        assert self.swap.contract_address == swap.p2sh_address
        return True

    def db_set_swap_status(self, status, finalize_swap=True):
        if self.db_swap is None:
            return False
        self.db_swap.status = status
        self.db_swap.updated_on = datetime.datetime.now()
        self.db_swap.save()
        if finalize_swap:
            self.db_swap = None
        return True

    def db_set_swap_txid(self, txid):
        if self.db_swap is None:
            return False
        self.db_swap.txid = txid
        self.db_swap.save()
        return True


async def main():
    taker = Taker(output_address='kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q')
    await taker.sat2kas(1)
    # await taker.kas2sat(1)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
