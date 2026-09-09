
import datetime
import os
import logging
import sys
import json

from peewee import DateTimeField, Model, CharField, BooleanField, IntegerField, TextField
from playhouse.migrate import SqliteDatabase


def resource_path(relative_path):
    # this is used to get the path to the database file, when the app is packaged with Pyinstaller (or other bundlers, maybe?)
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


# set peewee logger to warning
logging.getLogger('peewee').setLevel(logging.WARNING)
db_path = os.getenv('SATKAS_DB_PATH', None)
if db_path is None:
    db_path = resource_path('satkas.db')
db = SqliteDatabase(db_path, timeout=10)


class BaseModel(Model):
    class Meta:
        database = db

    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)


class Swap(BaseModel):
    swap_type = CharField(default='')
    # Indexed because a swap's key index is its row's position among its own
    # side's rows, so every derivation counts rows filtered by this.
    side = CharField(default='', index=True)  # maker / taker
    remote_pubkey = CharField(default='')
    ln_invoice = CharField(default=None, unique=True, null=True)
    payment_hash = CharField(default='')
    secret_hash = CharField(default='')
    sender_address = CharField(default='')
    receiver_address = CharField(default='')
    btc_sender_address = CharField(default='', null=True)
    btc_receiver_address = CharField(default='', null=True)
    contract = CharField(default='')
    btc_contract = CharField(default='', null=True)
    p2sh_address = CharField(default='')
    btc_p2sh_address = CharField(default='', null=True)
    dwork_amount = IntegerField(default=0)
    sat_amount = IntegerField(default=0)
    kas_locktime = IntegerField(null=True)  # daa score (on-chain) or ms timestamp (LN)
    btc_locktime = IntegerField(null=True)  # bitcoin block height
    dag_checkpoint_hash = CharField(null=True)  # virtual-chain start hash at OPENED
    # INIT → OPENED → FUNDED → COMPLETING|REFUNDING → COMPLETED|REFUNDED
    #                              ↘ EXPIRED | FAILED
    # OPENED = contracts ready, our pay not yet submitted.
    # FUNDED = our on-chain fund or LN pay submitted (capital at risk).
    status = CharField(default='INIT')
    txid = CharField(default=None, null=True)
    btc_txid = CharField(default=None, null=True)
    secret = CharField(default=None, null=True)
    output_address = CharField(null=True)  # kas redeem/refund destination
    btc_output_address = CharField(null=True)
    extra_info = TextField(null=True)  # JSON object for non-core / plugin data

    def get_extra(self):
        if not self.extra_info:
            return {}
        try:
            return json.loads(self.extra_info)
        except (TypeError, ValueError):
            return {}

    def set_extra(self, key, value):
        extra = self.get_extra()
        extra[key] = value
        self.extra_info = json.dumps(extra)


class WalletModel(BaseModel):
    mnemonic = CharField()
    is_encrypted = BooleanField(default=False)
    address_counter = IntegerField(default=0)
    next_address = CharField()
    # next_btc_address = CharField(default=None, null=True)


class MakerWallet(WalletModel):
    pass


class TakerWallet(WalletModel):
    pass


# not using p2p table yet
class P2PNode(BaseModel):
    pubkey = CharField()
    p2p_endpoint = CharField()
    swap_endpoint = CharField()


class Setting(BaseModel):
    """Application settings table for storing all app configuration."""
    name = CharField(unique=True, index=True)  # e.g., "service.kaspad.host"
    value = CharField(null=True)  # Store as string, cast when retrieving
    value_type = CharField(default='str')  # 'str', 'int', 'bool', 'json', 'float'

    def get_typed_value(self):
        """Return the value cast to the appropriate Python type."""
        if self.value is None:
            return None

        if self.value_type == 'int':
            return int(self.value)
        elif self.value_type == 'bool':
            return self.value.lower() in ('true', '1', 'yes', 'on')
        elif self.value_type == 'float':
            return float(self.value)
        elif self.value_type == 'json':
            import json
            return json.loads(self.value)
        else:  # 'str' or unknown
            return self.value

    @classmethod
    def set_value(cls, name, value, value_type='str'):
        """Set or update a setting value."""
        # Convert value to string for storage
        if value is None:
            str_value = None
        elif value_type == 'bool':
            str_value = 'true' if value else 'false'
        elif value_type == 'json':
            str_value = json.dumps(value)
        else:
            str_value = str(value)

        # Upsert the setting
        setting, created = cls.get_or_create(
            name=name,
            defaults={
                'value': str_value,
                'value_type': value_type
            }
        )

        if not created:
            setting.value = str_value
            setting.value_type = value_type
            setting.save()

        return setting

    @classmethod
    def get_value(cls, name, default=None):
        """Get a setting value with optional default."""
        try:
            setting = cls.get(cls.name == name)
            return setting.get_typed_value()
        except cls.DoesNotExist:
            return default

    @classmethod
    def get_all_settings(cls):
        """Get all settings as a dictionary."""
        settings = {}
        for setting in cls.select():
            settings[setting.name] = setting.get_typed_value()
        return settings


def initialize_db():
    db.connect()
    db.create_tables([
        Swap, MakerWallet, TakerWallet, Setting
    ], safe=True)


initialize_db()
