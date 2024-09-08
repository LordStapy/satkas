
import datetime

from peewee import DateTimeField, Model, CharField, BooleanField, IntegerField
from playhouse.migrate import SqliteDatabase


db = SqliteDatabase('db/satkas.db', timeout=10)


class BaseModel(Model):
    class Meta:
        database = db

    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)


class Swap(BaseModel):
    swap_type = CharField(default='')
    ln_invoice = CharField(default='')
    payment_hash = CharField(default='')
    sender_address = CharField(default='')
    receiver_address = CharField(default='')
    contract = CharField(default='')
    p2sh_address = CharField(default='')
    status = CharField(default='INIT')  # INIT / PENDING / COMPLETED / REFUNDED
    # ToDo: add txid field for redeem/refund tx


class WalletModel(BaseModel):
    mnemonic = CharField()
    is_encrypted = BooleanField(default=False)
    address_counter = IntegerField(default=0)
    next_address = CharField()


class MakerWallet(WalletModel):
    pass


class TakerWallet(WalletModel):
    pass


class P2PNode(BaseModel):
    pubkey = CharField()
    p2p_endpoint = CharField()
    swap_endpoint = CharField()


def initialize_db():
    db.connect()
    db.create_tables([Swap, MakerWallet, TakerWallet], safe=True)


initialize_db()
