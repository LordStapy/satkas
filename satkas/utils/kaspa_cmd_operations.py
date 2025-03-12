
import asyncio
import os
import time
import subprocess
import json
import logging


logger = logging.getLogger(__name__)


def run_cmd(cmd, shell=False):
    process = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = process.communicate()
    return out, err


def get_utxos_by_address(address):
        kaspactl = os.getenv('KASPACTL', 'kaspactl')
        kas_rpc_server = os.getenv('KAS_RPC_SERVER', '')
        cmd = f"{kaspactl} -a -s {kas_rpc_server} GetUtxosByAddresses '{address}'"
        out, err = run_cmd(cmd, shell=True)
        if err:
            logger.error(err.decode().strip())
        if out:
            out = json.loads(out.decode())
            res = out['getUtxosByAddressesResponse']['entries']
        else:
            res = False
        return res


def broadcast_transaction(rpc_transaction):
    kaspactl = os.getenv('KASPACTL', 'kaspactl')
    kas_rpc_server = os.getenv('KAS_RPC_SERVER', '')
    cmd = f"{kaspactl} -a -s {kas_rpc_server} SubmitTransaction '{rpc_transaction}' false"
    out, err = run_cmd(cmd, shell=True)
    try:
        out = json.loads(out.decode())
        if not out['submitTransactionResponse']['error']:
            res = out['submitTransactionResponse']['transactionId']
        else:
            # ToDo: handle as many errors as possible here
            logger.error('HEY!!!')
            logger.error(out['submitTransactionResponse']['error'])
            weird_error = 'one of the transaction sequence locks conditions was not met'
            if weird_error in out['submitTransactionResponse']['error']['message']:
                logger.warning("Retrying broadcast in 3 seconds")
                time.sleep(3)
                return broadcast_transaction(rpc_transaction)
            res = False
    except json.JSONDecodeError:
        logger.error(err.decode())
        res = False
    return res
