
import asyncio
import time
import json
import logging

try:
    from satkas.p2p.p2p_node import Node
except ImportError:
    from .p2p_node import Node


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class MakerNode(Node):
    def __init__(self, *args, endpoint=None, p2p_port=48888, **kwargs):
        super().__init__(*args, **kwargs)
        self.ping_message = None
        self.offers = {
            'sat2kas': {},
            'kas2sat': {}
        }
        self.p2p_port = p2p_port
        self.tor_controller = None

    async def start(self):
        ping_count = 0
        status_count = 0
        await self.update_ping_msg()
        await self.swapnode.init_server(start_hidden_service=False)
        self.swapnode.init_hidden_service(extra_port=(self.p2p_port, self.p2p_port))
        self.loop.create_task(self.swapnode.start(init_server=False))
        while self.swapnode.hidden_service is None:
            await asyncio.sleep(0.1)
        self.endpoint = f"{self.swapnode.hidden_service}:{self.p2p_port}"

        server = self.loop.create_server(self.handle_new_connection, 'localhost', self.p2p_port)
        self.loop.create_task(server)

        await self.status_check()

        while True:
            if status_count == 30:
                await self.status_check()
                ping_count += 1
                status_count = 0

            if self.swapnode.price_offers['sat2kas'].keys() != self.offers['sat2kas'].keys() or \
                    self.swapnode.price_offers['kas2sat'].keys() != self.offers['kas2sat'].keys():
                await self.update_ping_msg()
                await self.broadcast_message(self.ping_message)
                ping_count = 0
                status_count += 1
            elif ping_count == 60:
                await self.update_ping_msg()
                await self.broadcast_message(self.ping_message)
                ping_count = 0
                status_count += 1
            else:
                ping_count += 1
                status_count += 1

            await asyncio.sleep(1)

    async def read_message(self, transport, line):
        peer = transport
        if not line:
            return False
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.error('Json decode error')
            logger.error(line)
            return False
        except Exception as e:
            logger.error(e, exc_info=True)
            return False
        try:
            remote_pubkey = data['pubkey']
        except TypeError:
            logger.error('Pubkey error reading message')
            return False

        if not self.swapnode.verify_signature(data):
            return False

        logger.debug(f"[{self.short_pubkey}] Received {data['type']} from {remote_pubkey[:4]}.. via fd={peer._sock_fd}")

        await self.handle_incoming_message(data, remote_pubkey, peer)

    async def handle_incoming_message(self, data, remote_pubkey, peer):
        if data['type'] == 'client_hello':
            await self.handle_client_hello(data, remote_pubkey, peer)

        elif data['type'] == 'server_hello':
            await self.handle_server_hello(data, remote_pubkey, peer)

        elif data['type'] == 'server_ping':
            await self.handle_server_ping(data, remote_pubkey, peer)

    async def handle_client_hello(self, data, remote_pubkey, peer):
        if peer is None:
            logger.error(f"Error: {remote_pubkey} peer is None")
            # await asyncio.sleep(1)
        self.client_list[remote_pubkey] = peer
        del self.connected_peers[peer]
        self.connected_peers[remote_pubkey] = peer
        await self.send_hello(peer)

    async def handle_server_hello(self, data, remote_pubkey, peer):
        if remote_pubkey not in self.server_list.keys():
            self.server_list[remote_pubkey] = {
                'transport': peer,
                'last_ping': 0,
                'payload': data['payload']
            }
        elif self.server_list[remote_pubkey] is None:
            self.server_list[remote_pubkey] = {
                'transport': peer,
                'last_ping': 0,
                'payload': data['payload']
            }

        elif self.server_list[remote_pubkey]['transport'] is None:
            self.server_list[remote_pubkey] = {
                'transport': peer,
                'last_ping': 0,
                'payload': data['payload']
            }

        del self.connected_peers[peer]
        self.connected_peers[remote_pubkey] = peer
        logger.debug(f"Received server hello, peer is outbound: {peer in self.outbound_peers.values()}")
        if peer not in self.outbound_peers.values():
            logger.debug(f"Sending hello to {peer}")
            await self.send_hello(peer)
        else:
            self.outbound_peers[remote_pubkey] = peer

    async def handle_server_ping(self, data, remote_pubkey, peer):
        # ignore messages older than 1 minute
        if data['payload']['ts'] < time.time() - 60:
            return False

        if remote_pubkey not in self.server_list.keys() \
                and remote_pubkey != self.swapnode.node_pubkey.hex():
            logger.debug(f"[{self.short_pubkey}] Got server ping, adding node {remote_pubkey}")
            self.server_list[remote_pubkey] = {
                'transport': None,
                'last_ping': int(time.time()),
                'payload': data['payload']
            }

        broadcast_condition = self.server_list.get(remote_pubkey) and \
                              (self.server_list[remote_pubkey]['last_ping'] < time.time() - 30
                               or self.server_list[remote_pubkey]['payload']['sat2kas'] != data['payload']['sat2kas']
                               or self.server_list[remote_pubkey]['payload']['kas2sat'] != data['payload']['kas2sat'])

        if broadcast_condition:
            # logger.debug(f"[{self.short_pubkey}] - broadcasting ping from {remote_pubkey[:4]}...")
            self.server_list[remote_pubkey]['last_ping'] = int(time.time())
            self.server_list[remote_pubkey]['payload']['sat2kas'] = data['payload']['sat2kas']
            self.server_list[remote_pubkey]['payload']['kas2sat'] = data['payload']['kas2sat']
            asyncio.create_task(self.broadcast_message(json.dumps(data), excluded_peers=[peer, remote_pubkey]))

    async def update_ping_msg(self):
        msg_type = 'server_ping'
        self.offers['sat2kas'] = self.swapnode.price_offers['sat2kas']
        self.offers['kas2sat'] = self.swapnode.price_offers['kas2sat']
        ask_offers = [{'price': int(k), 'min_amount': v[1], 'max_amount': v[2]}
                      for k, v in self.offers['sat2kas'].items()]
        bid_offers = [{'price': int(k), 'min_amount': v[1], 'max_amount': v[2]}
                      for k, v in self.offers['kas2sat'].items()]
        payload = {
            'sat2kas': ask_offers,
            'kas2sat': bid_offers,
            'p2p_endpoint': self.endpoint,
            'swap_endpoint': self.swapnode.swap_endpoint,
            'onion': self.swapnode.hidden_service,
            'ts': time.time(),
            'features': {}
        }
        pubkey = self.swapnode.node_pubkey.hex()
        signature = self.swapnode.sign_message(msg_type, payload, node_key=True).hex()
        msg = {
            'type': msg_type,
            'payload': payload,
            'pubkey': pubkey,
            'signature': signature
        }
        self.ping_message = json.dumps(msg)
        # logger.debug(f"Ping msg: {self.ping_message}")

    async def broadcast_message(self, message='ping', excluded_peers=None):
        # logger.debug(f"Broadcasting msg: {message}")
        tasks = [self.send_message(transport, message)
                 for remote_pubkey, transport in self.connected_peers.items()
                 if (not (excluded_peers and (transport in excluded_peers or
                                              remote_pubkey in excluded_peers))
                     and isinstance(remote_pubkey, str))
                 ]
        res = await asyncio.gather(*tasks)

    async def send_message(self, transport, message):
        try:
            transport.write(message.encode().strip() + b'\n')
            # print(dir(transport))
        except ConnectionResetError:
            logger.debug(f"[{self.short_pubkey}] Peer {transport} disconnected")
            return False
        return True
