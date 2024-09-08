
import asyncio
import json
import os
import time
import logging
import itertools

from python_socks.async_.asyncio import Proxy
# from dotenv import load_dotenv

# load_dotenv()

logger = logging.getLogger('p2p_node')
logger.setLevel(logging.DEBUG)


class NodeConnection(asyncio.Protocol):
    node = None
    transport = None
    peername = None
    rport = None
    lport = None

    def __init__(self, node):
        self.node = node
        self.buffer = ''

    def connection_made(self, transport) -> None:
        peername = transport.get_extra_info('peername')
        self.transport = transport
        self.peername = peername
        self.rport = peername[1]
        self.lport = transport.get_extra_info('sockname')[1]
        logger.debug(f"{self.node.__class__.__name__}[{self.lport}] - Connection established with {peername}")
        self.node.connected_peers[transport] = transport

    def data_received(self, data: bytes) -> None:
        # split lines, sometimes socket is slow, so we get multiple messages at once
        data_string = data.decode().strip()
        splitted_lines = data_string.split('\n')
        for line in splitted_lines:
            try:
                json.loads(line)
            except json.JSONDecodeError:
                if line.startswith('{"type'):
                    self.buffer = line
                    return
                elif line.endswith('}'):
                    line = self.buffer + line
                    self.buffer = ''
                else:
                    return
            asyncio.create_task(self.node.read_message(self.transport, line))

    def connection_lost(self, exc: Exception | None) -> None:
        self.transport.close()
        self.node.handle_close_connection(self.transport)


class Node:
    def __init__(self, loop, swapnode=None, is_server=False, *args, **kwargs):
        # super().__init__(*args, **kwargs)
        self.transports = []
        self.known_peers = None
        self.connected_peers = {}
        self.outbound_peers = {}
        self.server = None
        self.is_server = is_server
        self.server_list = {}
        self.client_list = {}
        self.server_limit = 3
        self.loop = loop
        self.orderbook = {
            'sat2kas': {},
            'kas2sat': {}
        }
        self.swapnode = swapnode
        self.endpoint = None
        self.short_pubkey = f"{self.swapnode.node_pubkey.hex()[:4]}..{self.swapnode.node_pubkey.hex()[-4:]}"
        logger.info(f"Initiated node with node_key {self.swapnode.node_pubkey.hex()}")

    async def status_check(self, infinite_loop=False):
        logger.debug('Inside status check')
        if infinite_loop:
            await asyncio.sleep(5)
        while True:
            connected_servers = {k: x for k, x in self.server_list.items() if x and x['transport']}
            logger.debug(f"{len(connected_servers)} connected servers out of {len(self.server_list)}")
            if not len(connected_servers) and not len(self.server_list):
                await self.bootstrap_nodes()
            if len(connected_servers) < self.server_limit and len(self.server_list) > len(connected_servers):
                await self.add_more_nodes()

            timeout_servers = {k: x for k, x in self.server_list.items() if
                               x and x['last_ping'] and x['last_ping'] < time.time() - 300}
            if timeout_servers:
                logger.debug(f"Removing {len(timeout_servers)} servers, no ping received in 5 minutes")
                for k, v in timeout_servers.items():
                    peer = v['transport'] if v and v['transport'] else None
                    if peer:
                        peer.close()
                    del self.server_list[k]
            # await self.render_orderbook()
            if not infinite_loop:
                break
            await asyncio.sleep(30)

    # async def check_new_peers(self):
    #     while len(self.transports):
    #         node_connection = self.transports.pop()
    #         try:
    #             transport = node_connection.transport
    #         except AttributeError:
    #             transport = node_connection
    #         if transport is None:
    #             return False
    #         peername = transport.get_extra_info('peername')
    #         self.connected_peers[peername] = transport

    async def read_message(self, *args, **kwargs):
        raise NotImplementedError(f"read_message must be defined in subclass")

    async def send_message(self, *args, **kwargs):
        raise NotImplementedError(f"send_message must be defined in subclass")

    # async def connect_to_peer(self, host, port):
    #     reader, writer = await asyncio.open_connection(host, port)
    #     peername = writer.get_extra_info('peername')
    #     self.outbound_peers[peername] = {'reader': reader, 'writer': writer}

    async def connect_to_peer(self, host, port):
        if f"{host}:{port}" == self.endpoint:
            return False
        proxy = Proxy.from_url('socks5://127.0.0.1:9050', rdns=True)
        logger.debug(f"Connecting to {host}, {port}")
        try:
            sock = await proxy.connect(dest_host=host, dest_port=port)
            node_connection = NodeConnection(self)
            transport, protocol = await self.loop.create_connection(lambda: node_connection, sock=sock)
        except Exception as e:
            logger.error(e, exc_info=True)
            return False
        # print(transport, protocol)
        # peername = transport.get_extra_info('peername')
        self.outbound_peers[transport] = transport
        # self.server_list[peername] = {'transport': transport, 'last_ping': 0}
        # await asyncio.sleep(2)
        # self.server_list[remote_pubkey] = None
        response = await self.send_hello(transport)

    def handle_new_connection(self, *args, **kwargs):
        node_connection = NodeConnection(self)
        # self.transports.append(node_connection)
        return node_connection

    def handle_close_connection(self, peer):
        # del self.connected_peers[peername]
        try:
            to_remove = [(k, v) for k, v in self.connected_peers.items() if v == peer]
        except IndexError:
            to_remove = []
        for p in to_remove:
            del self.connected_peers[p[0]]
        try:
            for p in to_remove:
                del self.outbound_peers[p[0]]
        except KeyError:
            pass
        except IndexError:
            logger.error(f"handle_close_connection got Index error while deleting outbound peers: {to_remove}")
            pass

        key, transport = to_remove[0]
        if key in self.server_list.keys():
            del self.server_list[key]
        if key in self.client_list.keys():
            del self.client_list[key]
        logger.debug(f"[{self.short_pubkey}] Removed peer {key}")

    async def send_hello(self, transport):
        if self.__class__.__name__ == 'MakerNode':
            msg_type = 'server_hello'
            # this is probably bad codind, but I don't care, MakerNode subclass has the ping_message attribute
            payload = json.loads(self.ping_message)['payload']
        elif self.__class__.__name__ == 'TakerNode':
            msg_type = 'client_hello'
            payload = {}
        else:
            raise NotImplementedError('Wrong class type')

        pubkey = self.swapnode.node_pubkey
        signature = self.swapnode.sign_message(msg_type, payload, node_key=True)
        msg = json.dumps({
            'type': msg_type,
            'payload': payload,
            'pubkey': pubkey.hex(),
            'signature': signature.hex()
        })
        res = await self.send_message(transport, msg)

    async def bootstrap_nodes(self):
        seed_nodes = os.getenv('P2P_SEED_NODES', None)
        if seed_nodes:
            tasks = []
            for node in seed_nodes.split(','):
                host, port = node.split(':')
                tasks.append(self.connect_to_peer(host, int(port)))
            await asyncio.gather(*tasks)

    async def add_more_nodes(self):
        # first: check if we have non-connected servers:
        connected_servers = {k: x for k, x in self.server_list.items() if x and x['transport']}
        non_connected_servers = {k: x for k, x in self.server_list.items() if x and x['transport'] is None}

        while len(connected_servers) < self.server_limit and non_connected_servers:
            k, s = non_connected_servers.popitem()
            if not s or k == self.swapnode.node_pubkey:
                return
            logger.debug(f"[{self.short_pubkey}] - Connecting to {k}")
            payload = s['payload']
            if not payload:
                return
            p2p_endpoint = payload['p2p_endpoint']
            if not p2p_endpoint:
                # maker is not accepting p2p connections
                continue
            host, port = p2p_endpoint.split(':')
            self.server_list[k] = None
            res = await self.connect_to_peer(host, int(port))
            if not res:
                del self.server_list[k]
                continue
            connected_servers[k] = self.server_list[k]
            logger.debug(f"[{self.short_pubkey}] - Connected to server: {k}")

    async def update_orderbook(self, selected_amount=0):
        # clear orderbook
        self.orderbook = {
            'sat2kas': {},
            'kas2sat': {}
        }
        for key, server in self.server_list.items():
            if server is None:
                continue
            if server['payload']['sat2kas']:
                for ask in server['payload']['sat2kas']:
                    if not selected_amount or ask['min_amount'] <= selected_amount <= ask['max_amount']:
                        if not ask['price'] in self.orderbook['sat2kas'].keys():
                            self.orderbook['sat2kas'][ask['price']] = []
                        if server not in self.orderbook['sat2kas'][ask['price']]:
                            self.orderbook['sat2kas'][ask['price']].append(server)
                        else:
                            s_index = self.orderbook['sat2kas'][ask['price']].index(server)
                            self.orderbook['sat2kas'][ask['price']][s_index] = server
            if server['payload']['kas2sat']:
                for bid in server['payload']['kas2sat']:
                    if not selected_amount or bid['min_amount'] <= selected_amount <= bid['max_amount']:
                        if not bid['price'] in self.orderbook['kas2sat'].keys():
                            self.orderbook['kas2sat'][bid['price']] = []
                        if server not in self.orderbook['kas2sat'][bid['price']]:
                            self.orderbook['kas2sat'][bid['price']].append(server)
                        else:
                            s_index = self.orderbook['kas2sat'][bid['price']].index(server)
                            self.orderbook['kas2sat'][bid['price']][s_index] = server

        # add our offers for maker nodes:
        if self.__class__.__name__ == 'MakerNode':
            payload = json.loads(self.ping_message)['payload']
            # sat2kas
            for offer in payload['sat2kas']:
                if offer['price'] not in self.orderbook['sat2kas']:
                    self.orderbook['sat2kas'][offer['price']] = []
                ask_payload = {'payload': payload}
                self.orderbook['sat2kas'][offer['price']].append(ask_payload)
            for offer in payload['kas2sat']:
                if offer['price'] not in self.orderbook['kas2sat']:
                    self.orderbook['kas2sat'][offer['price']] = []
                bid_payload = {'payload': payload}
                self.orderbook['kas2sat'][offer['price']].append(bid_payload)

    async def render_orderbook(self, selected_amount=0, return_bidask=False):
        await self.update_orderbook(selected_amount=selected_amount)
        # ask
        asks = {}
        bids = {}
        for ask, servers in self.orderbook['sat2kas'].items():
            ask_servers = list(itertools.chain(*[s['payload']['sat2kas'] for s in servers]))
            min_amount = min([offer['min_amount'] for offer in ask_servers if offer['price'] == ask])
            all_max = [offer['max_amount'] for offer in ask_servers if offer['price'] == ask]
            max_amount = max(all_max)
            full_amount = sum(all_max)
            asks[ask] = f"{full_amount:>7d} ({min_amount}-{max_amount})"
        for bid, servers in self.orderbook['kas2sat'].items():
            bid_servers = list(itertools.chain(*[s['payload']['kas2sat'] for s in servers]))
            min_amount = min([offer['min_amount'] for offer in bid_servers if offer['price'] == bid])
            all_max = [offer['max_amount'] for offer in bid_servers if offer['price'] == bid]
            max_amount = max(all_max)
            full_amount = sum(all_max)
            bids[bid] = f"{full_amount:>7d} ({min_amount}-{max_amount})"

        # bids = dict(sorted(bids, reverse=True))
        # asks = dict(sorted(asks))

        bid_res = []
        ask_res = []

        output = f"{'BID':^25s} | {'ASK':^25s}\n"
        output += f" {'price'}  {'amount (min-max)':^17s} |  {'price'}  {'amount (min-max)':^17s}\n"
        for bid, ask in zip(sorted(bids, reverse=True), sorted(asks)):
            bid_str = f"{bid:>6d} {bids[bid]:<18s}"
            ask_str = f"{ask:>6d} {asks[ask]:<18s}"
            if return_bidask:
                bid_res.append(bid_str)
                ask_res.append(ask_str)
            output += f"{bid_str} | {ask_str}\n"

        if return_bidask:
            return bid_res, ask_res
        else:
            print(output)
