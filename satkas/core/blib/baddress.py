
import os

from bitcoinutils.keys import PublicKey
from bitcoinutils.setup import setup as btc_network_setup


def p2wpkh_address(pubkey):
    assert len(pubkey) == 33  # only compressed keys for P2WPKH
    btc_network_setup(os.getenv('BTC_NETWORK', 'mainnet'))
    pubkey = PublicKey(hex_str=pubkey.hex())
    return pubkey.get_segwit_address()
