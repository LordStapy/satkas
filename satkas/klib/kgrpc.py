
import os
import grpc

from google.protobuf import json_format

from satkas.klib.messages_pb2_grpc import RPCStub
from satkas.klib.messages_pb2 import KaspadRequest


def serialize_rpc_request(base_req, command, payload=None):
    if payload is None:
        payload = {}
    if isinstance(payload, dict):
        json_format.ParseDict(payload, command)
    if isinstance(payload, str):
        json_format.Parse(payload, command)
    command.SetInParent()
    return base_req


def run_grpc_command(rpc_requests, rpc_server=None):
    if rpc_server is None:
        rpc_server = os.getenv('KAS_RPC_SERVER')
    if ':' not in rpc_server:
        # if port is no specified, defaults to mainnet
        rpc_server += ':16110'
    channel = grpc.insecure_channel(
            rpc_server,
            options=[
                ('grpc.max_send_message_length', -1),
                ('grpc.max_receive_message_length', (1024**2)*8)
            ]
    )
    stub = RPCStub(channel)
    if not isinstance(rpc_requests, list):
        rpc_requests = [rpc_requests]
    resp = stub.MessageStream((r for r in rpc_requests))
    results = []
    for r in resp:
        results.append(json_format.MessageToDict(r))
    channel.close()
    return results


def base_request(method_name, payload=None, **kwargs):
    base_req = KaspadRequest()
    req = getattr(base_req, f"{method_name}Request")
    serialized_req = serialize_rpc_request(base_req, req, payload)
    results = run_grpc_command(serialized_req, **kwargs)
    return results[0][f"{method_name}Response"]


def getUtxosByAddresses(addresses=None, **kwargs):
    if isinstance(addresses, str):
        addresses = [addresses]
    payload = {'addresses': addresses}
    return base_request('getUtxosByAddresses', payload, **kwargs)


def submitTransaction(rpc_tx, allow_orphan=True, **kwargs):
    payload = {'transaction': rpc_tx, 'allowOrphan': allow_orphan}
    return base_request('submitTransaction', payload, **kwargs)


def getBlockDagInfo(**kwargs):
    return base_request('getBlockDagInfo', **kwargs)


def getUtxoReturnAddress(txid, daa, **kwargs):
    payload = {'txid': txid, 'accepting_block_daa_score': daa}
    return base_request('GetUtxoReturnAddress', payload, **kwargs)


if __name__ == '__main__':
    print(getUtxosByAddresses('kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q'))
    print(getBlockDagInfo())
