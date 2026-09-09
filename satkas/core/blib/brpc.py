import asyncio
import datetime
import logging
import os
import json
import aiohttp

logger = logging.getLogger('brpc')


class BrpcError(Exception):
    """Raised when a bitcoind JSON-RPC call returns an error object."""

    def __init__(self, error):
        self.error = error
        if isinstance(error, dict):
            message = error.get('message', str(error))
            code = error.get('code')
            super().__init__(f"RPC error {code}: {message}" if code is not None else message)
        else:
            super().__init__(str(error))


def raise_for_error(res):
    """Raise BrpcError if the RPC response contains an error. Returns res unchanged otherwise."""
    if res is None:
        raise BrpcError({'message': 'empty RPC response'})
    error = res.get('error')
    if error is not None:
        raise BrpcError(error)
    return res


async def base_req(method, *args, **kwargs):
    server = kwargs.pop('server', None)
    if server is None:
        host = os.getenv('BITCOIND_HOST', '127.0.0.1')
        port = os.getenv('BITCOIND_PORT', '8332')
        server = f"{host}:{port}"
    user = kwargs.pop('user', None)
    if user is None:
        user = os.getenv('BTC_RPC_USER', '')
    password = kwargs.pop('password', None)
    if password is None:
        password = os.getenv('BTC_RPC_PASS', '')

    endpoint = f"http://{server}"

    wallet = kwargs.pop('rpcwallet', None)
    if wallet is not None:
        endpoint += f"/wallet/{wallet}"
    verbose = kwargs.pop('verbose_print', False)

    payload = {
        'jsonrpc': '2.0',
        'id': '0',
        'method': method,
        'params': list(args) if args else kwargs
    }

    headers = {
        'Content-Type': 'application/json'
    }

    auth = aiohttp.BasicAuth(user, password)

    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, auth=auth, data=json.dumps(payload), headers=headers) as response:
            res = await response.json()
            if verbose:
                logger.debug(res)
            return res


async def createwallet(
    name,
    disable_private_keys=True,
    blank=False,
    passphrase='',
    avoid_reuse=False,
    descriptors=True,
    load_on_startup=True,
    **kwargs
):
    return await base_req(
        'createwallet',
        name,
        disable_private_keys,
        blank,
        passphrase,
        avoid_reuse,
        descriptors,
        load_on_startup,
        **kwargs
    )


async def loadwallet(name, load_on_startup=True, **kwargs):
    return await base_req('loadwallet', name, load_on_startup, **kwargs)


async def listwallets(**kwargs):
    return await base_req('listwallets', **kwargs)


async def getwalletinfo(**kwargs):
    return await base_req('getwalletinfo', **kwargs)


async def walletpassphrase(passphrase, timeout=60, **kwargs):
    return await base_req('walletpassphrase', passphrase, timeout, **kwargs)


async def walletlock(**kwargs):
    return await base_req('walletlock', **kwargs)


async def getbalance(*args, **kwargs):
    return await base_req('getbalance', *args, **kwargs)


async def getnewaddress(label='', address_type=None, **kwargs):
    if address_type is not None:
        return await base_req('getnewaddress', label, address_type, **kwargs)
    return await base_req('getnewaddress', label, **kwargs)


async def scantxoutset(action, descriptors):
    return await base_req('scantxoutset', action, descriptors)


async def importdescriptors(descriptors, ts=None, **kwargs):
    if not isinstance(descriptors, list):
        descriptors = [descriptors]
    if ts is None:
        ts = int((datetime.datetime.now() - datetime.timedelta(hours=1)).timestamp())
    payload = [
        {
            'desc': desc,
            'timestamp': ts
        }
        for desc in descriptors
    ]
    return await base_req('importdescriptors', payload, **kwargs)


async def listunspent(addresses=None, minconf=1, maxconf=9999999, include_unsafe=True, query_options=None, **kwargs):
    if addresses is None:
        addresses = []
    if not isinstance(addresses, list):
        addresses = [addresses]
    if query_options is None:
        query_options = {}
    payload = (minconf, maxconf, addresses, include_unsafe, query_options)
    return await base_req('listunspent', *payload, **kwargs)


async def getdescriptorinfo(descriptor, **kwargs):
    return await base_req('getdescriptorinfo', descriptor, **kwargs)


async def deriveaddresses(descriptor, **kwargs):
    return await base_req('deriveaddresses', descriptor, **kwargs)


async def getaddressinfo(address, **kwargs):
    return await base_req('getaddressinfo', address, **kwargs)


async def listtransactions(label=None, count=10, skip=0, include_watchonly=True, **kwargs):
    if label is None:
        label = '*'
    return await base_req('listtransactions', label, count, skip, include_watchonly, **kwargs)


async def gettransaction(txid, include_watchonly=True, verbose=True, **kwargs):
    return await base_req('gettransaction', txid, include_watchonly, verbose, **kwargs)


async def getrawtransaction(txid, verbose=True, blockhash=None, **kwargs):
    if blockhash is not None:
        return await base_req('getrawtransaction', txid, verbose, blockhash, **kwargs)
    return await base_req('getrawtransaction', txid, verbose, **kwargs)


async def gettxout(txid, vout, include_mempool=True, **kwargs):
    return await base_req('gettxout', txid, vout, include_mempool, **kwargs)


async def gettxspendingprevout(outputs, **kwargs):
    """
    Scan mempool (and confirmed chain if -txspenderindex) for txs spending the given outputs.
    outputs: list of {"txid": "...", "vout": n}
    """
    return await base_req('gettxspendingprevout', outputs, **kwargs)


async def getblockchaininfo(**kwargs):
    return await base_req('getblockchaininfo', **kwargs)


async def sendtoaddress(address, amount, *args, **kwargs):
    # TODO: manage encrypted wallets, need to call walletpassphrase before submitting this command
    return await base_req('sendtoaddress', address, amount, *args, **kwargs)


async def generatetoaddress(address, nblocks=1, **kwargs):
    return await base_req('generatetoaddress', nblocks, address, **kwargs)


async def sendrawtransaction(raw_transaction, **kwargs):
    res = await base_req('sendrawtransaction', raw_transaction, **kwargs)
    if res.get('error'):
        logger.error(f"sendrawtransaction failed: {res['error']}")
    else:
        logger.info(f"sendrawtransaction succeeded: txid={res.get('result')}")
    return res


async def getblockcount(**kwargs):
    return await base_req('getblockcount', **kwargs)


async def estimatesmartfee(conf_target, estimate_mode='CONSERVATIVE', **kwargs):
    return await base_req('estimatesmartfee', conf_target, estimate_mode, **kwargs)
