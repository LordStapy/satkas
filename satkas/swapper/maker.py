
import asyncio
import os
import time
import json
import math
import logging

from aiohttp import web
from stem.control import Controller

from satkas.db.models import MakerWallet, Swap
from satkas.swapper.counterparty import Counterparty
from satkas.swapper.atomic_swap import AtomicSwap


logger = logging.getLogger('maker')


class Maker(Counterparty):
    def __init__(self,
                 output_address=None,
                 wallet_index=1,
                 lport=38080,
                 apiport=None,
                 swap_endpoint=None):
        super().__init__(wallet_db_table=MakerWallet,
                         keep_unlocked=True,
                         wallet_index=wallet_index,
                         swap_endpoint=swap_endpoint)
        self.server = None
        self.runner = None
        self.site = None
        self.lport = lport
        self.apiserver = None
        self.apirunner = None
        self.apisite = None
        self.apiport = apiport
        self.tor_controller = None
        self.hidden_service = None

        if output_address is None:
            self.output_address = self.address
        else:
            self.output_address = output_address

        self.price_offers = {
            'sat2kas': {},
            'kas2sat': {}
        }
        self.locked_offers = {}
        self.valid_until = int(os.getenv('OFFER_VALIDITY_SECONDS', 30))

        # swaps format: {'contract_address': swap_object}
        self.swaps = {
            'sat2kas': {},
            'kas2sat': {}
        }

        self.refresh_delay = int(os.getenv('MAKER_REFRESH_DELAY', 5))

    async def start(self, init_server=True):
        if init_server:
            await self.init_server()
        logger.info('Maker server initialized')
        try:
            while True:
                try:
                    await self.update_offers()
                except Exception as e:
                    logger.error(e, exc_info=True)
                try:
                    await self.monitor_swaps()
                except Exception as e:
                    logger.error(e, exc_info=True)
                await asyncio.sleep(self.refresh_delay)
        finally:
            self.remove_hidden_service()

    async def init_server(self, start_hidden_service=True):
        self.server = web.Server(self.post_handler)
        self.runner = web.ServerRunner(self.server)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', self.lport)
        await self.site.start()

        if self.apiport is not None:
            self.apiserver = web.Server(self.api_handler)
            self.apirunner = web.ServerRunner(self.apiserver)
            await self.apirunner.setup()
            self.apisite = web.TCPSite(self.apirunner, '127.0.0.1', self.apiport)
            await self.apisite.start()
        if start_hidden_service:
            self.init_hidden_service()

    def init_hidden_service(self, extra_port=None):
        key_path = os.getenv('HIDDEN_SERVICE_KEY_PATH')
        self.tor_controller = Controller.from_port()
        self.tor_controller.authenticate()
        hidden_service_ports = {80: self.lport}
        if extra_port:
            hidden_service_ports[extra_port[0]] = extra_port[1]
        if not os.path.exists(key_path):
            service = self.tor_controller.create_ephemeral_hidden_service(
                hidden_service_ports,
                await_publication=True
            )
            with open(key_path, 'w') as key_file:
                key_file.write(f"{service.private_key_type}:{service.private_key}")
        else:
            with open(key_path) as key_file:
                key_type, key_content = key_file.read().split(':', 1)
            logger.info(f"Starting hidden service with key type: {key_type}")
            service = self.tor_controller.create_ephemeral_hidden_service(
                hidden_service_ports,
                key_type=key_type,
                key_content=key_content,
                await_publication=True,
                detached=False
            )
        self.hidden_service = f"{service.service_id}.onion"
        logger.info(f"Started hidden service with address {self.hidden_service}")

    def remove_hidden_service(self):
        self.tor_controller.remove_ephemeral_hidden_service(self.hidden_service[:-6])

    async def api_handler(self, request):
        content = await request.content.read()
        path = request.path
        logger.debug(f"{path}: {content}")
        if path == '/update_offers':
            offers = json.loads(content)
            logger.info(offers)
            self.price_offers['sat2kas'] = offers.get('sat2kas', {})
            self.price_offers['kas2sat'] = offers.get('kas2sat', {})
            return web.json_response(self.price_offers)
        return web.json_response({'error': 'Something went wrong'})

    async def post_handler(self, request):
        content = await request.content.read()
        try:
            data = json.loads(content)
            logger.info(f"Received data: {data}")
        except json.JSONDecodeError:
            logger.error(f"Error decoding request: {request} Content: {content}")
            return web.Response(text='Nope')

        if not self.verify_signature(data):
            return web.Response(text='Signature verification failed')

        msg_type = data['type']
        msg_payload = data['payload']
        remote_pubkey = data['pubkey']

        match msg_type:
            case 'price':
                response_payload = await self.handle_price_req(msg_payload, remote_pubkey)
                if not response_payload:
                    return web.json_response({'error': 'Wrong swap_type'})
            case 'init_swap':
                response_payload = await self.handle_init_swap(msg_payload, remote_pubkey)
                if not response_payload:
                    return web.json_response({'error': 'Failed swap init'})
            case _:
                return web.json_response({'error': 'Method not implemented'})

        response_signature = self.sign_message(msg_type, response_payload, node_key=True)
        response = {
            'type': msg_type,
            'payload': response_payload,
            'pubkey': self.node_pubkey.hex(),
            'signature': response_signature.hex(),
            'error': None
        }
        return web.json_response(response)

    async def handle_price_req(self, msg_payload, remote_pubkey):
        self.locked_offers[remote_pubkey] = {
            'sat2kas': {'offers': None, 'valid_until': 0},
            'kas2sat': {'offers': None, 'valid_until': 0}
        }
        response_payload = {'type': 'swap_price'}
        valid_until = int(time.time()) + self.valid_until
        if msg_payload['swap_type'] == 'sat2kas':
            if p2p_price := msg_payload.get('p2p_price'):
                offer = self.price_offers['sat2kas'].get(p2p_price)
                response_payload['price'] = offer
                offers = [offer]
            elif sat_amount := msg_payload.get('amount'):
                logger.info('Loading offers')
                for offer in self.price_offers['sat2kas'].values():
                    logger.info(f"{offer}")
                    _price, _min, _max = offer
                    kas_amount = int(int(sat_amount) / _price)
                    logger.info(f"{kas_amount}")
                    if _min <= kas_amount <= _max:
                        response_payload['amount'] = kas_amount
                        response_payload['price'] = _price
                        offers = [offer]
                        logger.info('Offer found')
                        break
                else:
                    response_payload['amount'] = 0
                    offers = []
            else:
                offers = self.price_offers['sat2kas']
                response_payload['offers'] = offers
            if isinstance(offers, dict):
                self.locked_offers[remote_pubkey]['sat2kas']['offers'] = [v for v in offers.values()]
            elif isinstance(offers, list):
                self.locked_offers[remote_pubkey]['sat2kas']['offers'] = offers
            else:
                return False
            self.locked_offers[remote_pubkey]['sat2kas']['valid_until'] = valid_until
        elif msg_payload['swap_type'] == 'kas2sat':
            if p2p_price := msg_payload.get('p2p_price'):
                offer = self.price_offers['kas2sat'].get(p2p_price)
                response_payload['price'] = offer
                offers = [offer]
            elif kas_amount := msg_payload.get('amount'):
                logger.info('Loading offers')
                for offer in self.price_offers['kas2sat'].values():
                    logger.info(f"{offer}")
                    _price, _min, _max = offer
                    sat_amount = int(int(kas_amount) * _price)
                    logger.info(f"{sat_amount}")
                    if _min <= int(kas_amount) <= _max:
                        response_payload['amount'] = sat_amount
                        response_payload['price'] = _price
                        offers = [offer]
                        logger.info('Offer found')
                        break
                else:
                    response_payload['amount'] = 0
                    offers = []
            elif sat_amount := msg_payload.get('sat_amount'):
                for offer in self.price_offers['kas2sat'].values():
                    _price, _min, _max = offer
                    kas_amount = round(sat_amount / _price, 3)
                    if _min <= kas_amount <= _max:
                        response_payload['kas_amount'] = kas_amount
                        response_payload['price'] = _price
                        offers = [offer]
                        logger.info('Offer found')
                        break
                else:
                    response_payload['kas_amount'] = 0
                    offers = []
            else:
                offers = self.price_offers['kas2sat']
                response_payload['offers'] = offers
            if isinstance(offers, dict):
                self.locked_offers[remote_pubkey]['kas2sat']['offers'] = [v for v in offers.values()]
            elif isinstance(offers, list):
                self.locked_offers[remote_pubkey]['kas2sat']['offers'] = offers
            else:
                return False
            self.locked_offers[remote_pubkey]['kas2sat']['valid_until'] = valid_until
        else:
            return False
        response_payload['valid_until'] = int(time.time()) + self.valid_until
        return response_payload

    async def handle_init_swap(self, msg_payload, remote_node_pubkey):
        count = Swap.select().count()
        db_swap = Swap.create()
        address = self.get_next_address(n_key=count+1)
        self.update_address_counter(new_counter=count+1)

        logger.debug(f"Init swap from {remote_node_pubkey}, payload: {msg_payload}")

        # case 1 sat2kas
        if 'receiver_address' in msg_payload:
            swap_type = 'sat2kas'
            swap_price = msg_payload['price']
            receiver_address = msg_payload['receiver_address']
            kas_amount = msg_payload['kas_amount']
            try:
                locked_offers = self.locked_offers[remote_node_pubkey]['sat2kas']['offers']
            except KeyError:
                locked_offers = []
            for locked_offer in locked_offers:
                price, min_amt, max_amt = locked_offer
                if swap_price >= price and min_amt <= kas_amount <= max_amt:
                    break
            else:
                # if swap price is not matched in locked offers, check price offers
                p2p_price = math.ceil(swap_price)
                offer = self.price_offers['sat2kas'].get(p2p_price)
                if not (offer and swap_price >= offer[0] and offer[1] <= kas_amount <= offer[2]):
                    return False
            sat_amount = int(kas_amount * swap_price)
            ln_invoice = await self.gen_ln_invoice(sat_amount, lncli=os.getenv('LNCLI', None))
            sender_address = address
            sender_private_key = None
            receiver_private_key = None
            response_payload = {
                'sender_address': address,
                'ln_invoice': ln_invoice,
            }
            counterparty_short_pubkey = f"{remote_node_pubkey[:3]}...{remote_node_pubkey[-3:]}"
            logger.info(f"Got sat2kas request from {counterparty_short_pubkey} with address: {receiver_address}")
        # case 2 kas2sat
        elif 'sender_address' in msg_payload:
            swap_type = 'kas2sat'
            swap_price = msg_payload['price']
            ln_invoice = msg_payload['ln_invoice']
            decoded_invoice = AtomicSwap().decode_ln_invoice(ln_invoice)
            sat_amount = int(decoded_invoice['num_satoshis'])
            kas_amount = round(sat_amount / swap_price, 3)
            try:
                locked_offers = self.locked_offers[remote_node_pubkey]['kas2sat']['offers']
            except KeyError:
                locked_offers = []
            for locked_offer in locked_offers:
                price, min_amt, max_amt = locked_offer
                if swap_price <= price and min_amt <= kas_amount <= max_amt:
                    break
            else:
                # if swap price is not matched in locked offers, check price offers
                p2p_price = math.floor(swap_price)
                offer = self.price_offers['kas2sat'].get(p2p_price)
                if not (offer and swap_price <= offer[0] and offer[1] <= kas_amount <= offer[2]):
                    return False
            sender_address = msg_payload['sender_address']
            receiver_address = address
            sender_private_key = None
            receiver_private_key = None
            response_payload = {
                'receiver_address': address,
                'kas_amount': kas_amount
            }
            counterparty_short_pubkey = f"{remote_node_pubkey[:3]}...{remote_node_pubkey[-3:]}"
            logger.info(f"Got kas2sat request from {counterparty_short_pubkey} with address: {sender_address}")
        else:
            return False

        swap = AtomicSwap(
            ln_rpc_server=os.getenv('LN_RPC_SERVER', '127.0.0.1:10009'),
            kas_rpc_server=os.getenv('KAS_RPC_SERVER', '127.0.0.1'),
            invoice=ln_invoice,
            sender_address=sender_address,
            sender_private_key=sender_private_key,
            receiver_address=receiver_address,
            receiver_private_key=receiver_private_key,
            output_address=self.output_address
        )

        decode_out = swap.decode_ln_invoice()
        logger.info(decode_out)
        if swap_type == 'kas2sat':
            cmd = os.getenv('LNCLI', 'lncli')
            if ln_rpc_server := os.getenv('LN_RPC_SERVER', ''):
                cmd += f" --rpcserver {ln_rpc_server}"
            cmd += f" getinfo"
            out, err = swap.run_cmd(cmd.split())
            if out:
                out = json.loads(out.decode())
                if out['identity_pubkey'] == decode_out['destination']:
                    logger.error('Payment to ourself, aborting...')
                    return False
            else:
                logger.error(err)
        if swap.timelock / 1000 < time.time():
            # invoice is already expired, abort swap
            return False
        swap.gen_contract_address()

        self.swaps[swap_type][swap.contract_address] = swap

        db_swap.swap_type = swap_type
        db_swap.ln_invoice = ln_invoice
        db_swap.payment_hash = swap.secret_hash.hex()
        db_swap.sender_address = sender_address
        db_swap.receiver_address = receiver_address
        db_swap.contract = swap.contract_script.hex()
        db_swap.p2sh_address = swap.contract_address
        db_swap.status = 'PENDING'
        db_swap.save()

        response_payload['p2sh_address'] = swap.contract_address
        response_payload['contract'] = swap.contract_script.hex()

        match swap_type:
            case 'sat2kas':
                asyncio.create_task(
                    self.fund_contract_address(
                        swap.contract_address,
                        amount=kas_amount
                    )
                )
            case 'kas2sat':
                swap.kas_amount = kas_amount

        return response_payload

    async def monitor_swaps(self):
        s2k_tasks = [self.monitor_sat2kas(swap) for swap in self.swaps['sat2kas'].values()]
        s2k_results = await asyncio.gather(*s2k_tasks)

        k2s_tasks = [self.monitor_kas2sat(swap) for swap in self.swaps['kas2sat'].values()]
        k2s_results = await asyncio.gather(*k2s_tasks)

        # maybe do something with results? Don't know yet
        try:
            if [t for t in s2k_results if t]:
                logger.debug('Completed s2k swap')
            if [t for t in k2s_results if t]:
                logger.debug('Completed k2s swap')
        except Exception as e:
            logger.error(e, exc_info=True)

    async def monitor_sat2kas(self, swap):
        # refund path
        db_swap = Swap.select().where(Swap.p2sh_address == swap.contract_address)
        if len(db_swap):
            db_swap = db_swap[0]
        # print(f"Monitoring db swap n. {db_swap}")
        utxo_sum = await swap.async_check_utxo(timeout=False)
        # wait 3 minutes after timelock expiry to refund the contract
        if utxo_sum and swap.timelock + 180000 < time.time() * 1000:
            swap.sender_private_key = self.get_secret_key(db_swap.id)
            res = swap.spend_contract()
            if res:
                db_swap.status = 'REFUNDED'
                db_swap.save()
                # self.update_address_counter()
                del self.swaps['sat2kas'][swap.contract_address]
                logger.info(f"Exiting after refund\n{'-'*10}\n")
                return
        # redeem path
        # check if invoice was paid, then end swap
        if swap.timelock > time.time() * 1000:
            cmd = f"lncli --rpcserver {os.getenv('LN_RPC_SERVER', '127.0.0.1:10009')} lookupinvoice " \
                  f"{swap.secret_hash.hex()}"
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await proc.communicate()

            try:
                out_data = json.loads(stdout.decode())
                if out_data['state'] == 'SETTLED':
                    if not swap.get_utxos_by_address(swap.contract_address):
                        db_swap.status = 'COMPLETED'
                        db_swap.save()
                        logger.info(f"Exiting after redeem\n{'-'*10}\n")
                        # self.update_address_counter()
                        del self.swaps['sat2kas'][swap.contract_address]
                    return
            except json.JSONDecodeError:
                if 'unable to locate invoice' in stderr.decode():
                    logger.debug(f"Invoice expired and removed, exiting")
                else:
                    logger.error(f"stdout: {stdout.decode()}\n stderr: {stderr.decode()}")

    async def monitor_kas2sat(self, swap):
        if not swap.kas_amount:
            return
        db_swap = Swap.select().where(Swap.p2sh_address == swap.contract_address)
        if len(db_swap):
            db_swap = db_swap[0]
        utxo_sum = await swap.async_check_utxo(swap.contract_address, min_amount=swap.kas_amount + 0.001, timeout=False)
        if not utxo_sum:
            # check if invoice is expired
            # if yes, then delete swap from list and set it as EXPIRED in db
            if int(swap.timelock / 1e3 - time.time()) < 0:
                db_swap.status = 'EXPIRED'
                db_swap.save()
                del self.swaps['kas2sat'][swap.contract_address]
                logger.info(f"Exiting after invoice expiry\n{'-'*10}\n")
            return

        # wait for N confirmations before sweeping the P2SH utxo(s)
        if not swap.check_daa_confirmations():
            return

        secret = await self.lncli_pay(swap.invoice, lncli=os.getenv('LNCLI', None))
        if not secret:
            return
        secret_bytes = bytes.fromhex(secret)
        swap.receiver_private_key = self.get_secret_key(n_key=db_swap.id)
        res = swap.spend_contract(secret=secret_bytes)
        if res:
            db_swap.status = 'COMPLETED'
            db_swap.save()
            del self.swaps['kas2sat'][swap.contract_address]
            logger.info(f"Exiting after redeem\n{'-'*10}\n")
            return True

    async def update_offers(self):
        pass


async def main():
    maker = Maker(output_address='kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q')
    await maker.start()


if __name__ == '__main__':
    asyncio.run(main())
