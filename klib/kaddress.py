
import os
import hashlib

# from dotenv import load_dotenv

from .kbech32 import encode_address, decode_address

# load_dotenv()

NETWORK_PREFIX = os.getenv('KAS_NETWORK_PREFIX', 'kaspa')


def get_script_hash(script):
    script_hash = hashlib.blake2b(script, digest_size=32).digest()
    return script_hash


def p2sh_address_from_script_hash(script_hash):
    if isinstance(script_hash, bytes):
        script_hash = [i for i in script_hash]
    version = 8
    return encode_address(NETWORK_PREFIX, script_hash, version)


def p2sh_address_from_script(script):
    if isinstance(script, list):
        script = bytes(script)
    script_hash = get_script_hash(script)
    return p2sh_address_from_script_hash(script_hash)


def get_pubkey_hash(pubkey):
    pubkey_hash = hashlib.blake2b(pubkey, digest_size=32).digest()
    return pubkey_hash


def p2pk_address(pubkey):
    if isinstance(pubkey, bytes):
        pubkey = [i for i in pubkey]
    version = 0
    return encode_address(NETWORK_PREFIX, pubkey, version)

