
import json
import time
import logging

try:
    from p2p.p2p_node import Node
except ImportError:
    from p2p_node import Node


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class TakerNode(Node):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.loop.create_task(self.connect_to_peer2('localhost', 48888))

    async def read_message(self, transport, line):
        if not line:
            return False
        try:
            data = json.loads(line)
            short_pubkey = f"{data['pubkey'][:3]}...{data['pubkey'][-3:]}"
            logger.debug(f"[{self.short_pubkey}] TakerNode.read_message: "
                         f"{data['type']}, {data['payload']}, {short_pubkey}")

            remote_pubkey = data['pubkey']

            if not self.swapnode.verify_signature(data):
                return False

            await self.handle_incoming_message(data, remote_pubkey, transport)

        except json.JSONDecodeError:
            return False
        except Exception as e:
            logger.error(e)
            return False

    async def handle_incoming_message(self, data, remote_pubkey, peer):
        if data['type'] == 'client_hello':
            pass
        elif data['type'] == 'server_hello':
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
            self.outbound_peers[remote_pubkey] = peer
        elif data['type'] == 'server_ping':
            if remote_pubkey not in self.server_list.keys():
                if remote_pubkey == self.swapnode.node_pubkey:
                    return
                self.server_list[remote_pubkey] = {
                    'transport': None,
                    'last_ping': int(time.time()),
                    'payload': data['payload']
                }
            else:
                self.server_list[remote_pubkey]['payload'] = data['payload']
                self.server_list[remote_pubkey]['last_ping'] = int(time.time())

    async def send_message(self, transport, message):
        try:
            transport.write(message.encode())
        except Exception as e:
            logger.error(e)
