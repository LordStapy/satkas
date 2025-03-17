
import grpc
import json

from grpc._cython.cygrpc import CompressionAlgorithm
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


def run_grpc_command(rpc_requests, rpc_server='192.168.1.103:16110'):
    channel = grpc.insecure_channel(
            rpc_server,
            options=[
                ('grpc.max_send_message_length', -1),
                ('grpc.max_receive_message_length', (1024**2)*4),
                ('grpc.default_compression_algorithm', CompressionAlgorithm.gzip),
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


def base_request(method_name, payload=None):
    base_req = KaspadRequest()
    req = getattr(base_req, f"{method_name}Request")
    serialized_req = serialize_rpc_request(base_req, req, payload)
    results = run_grpc_command(serialized_req)
    return results[0][f"{method_name}Response"]


def getUtxosByAddresses(addresses=None):
    if isinstance(addresses, str):
        addresses = [addresses]
    payload = {'addresses': addresses}
    return base_request('getUtxosByAddresses', payload)


def submitTransaction(rpc_tx, allow_orphan=True):
    payload = {'transaction': rpc_tx, 'allowOrphan': allow_orphan}
    return base_request('submitTransaction', payload)


def getBlockDagInfo():
    return base_request('getBlockDagInfo')


if __name__ == '__main__':
    print(getUtxosByAddresses(['kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q']))
    print(getBlockDagInfo())
    print(submitTransaction({'a': 'b'}))
    # cmd = KaspadRequest().getUtxosByAddressesRequest
    # print(run_grpc_command(cmd, {'addresses': ['kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q']}))
    #
    # cmd = KaspadRequest().getBlockDagInfoRequest
    # print(run_grpc_command(cmd, {}))
