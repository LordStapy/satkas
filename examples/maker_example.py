
import asyncio
import logging
import time
from math import ceil, floor

logging.basicConfig(level=logging.DEBUG)

from dotenv import load_dotenv

# install ccxt
import ccxt.async_support as ccxt

from satkas.swapper.maker import Maker
from satkas.p2p.maker_p2p_node import MakerNode


load_dotenv()

logger = logging.getLogger('satkas_maker')
logging.getLogger('ccxt').setLevel(logging.WARNING)


class DynamicMaker(Maker):
    """
    Maker subclass with automatic update of offers based on cex price
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    # define update_offers method
    async def update_offers(self):
        # clean expired offers, maker code doesn't currently handle this
        remote_keys = [k for k in self.locked_offers.keys()]
        for remote_key in remote_keys:
            if self.locked_offers[remote_key]['sat2kas']['valid_until'] < time.time():
                self.locked_offers[remote_key]['sat2kas']['offers'] = None
                self.locked_offers[remote_key]['sat2kas']['valid_until'] = 0
            if self.locked_offers[remote_key]['kas2sat']['valid_until'] < time.time():
                self.locked_offers[remote_key]['kas2sat']['offers'] = None
                self.locked_offers[remote_key]['kas2sat']['valid_until'] = 0
            if (
                (self.locked_offers[remote_key]['sat2kas']['valid_until'] == 0) and
                (self.locked_offers[remote_key]['kas2sat']['valid_until'] == 0)
            ):
                del self.locked_offers[remote_key]

        # get current rate from cex, e.g. kucoin
        exc = ccxt.kucoin()
        kasbtc_ticker = await exc.fetch_ticker('KAS/BTC')
        await exc.close()

        # update offers with 2 sat spread, prices are expressed in satoshis per KAS
        # bid format (kas2sat): {floor(price): (2_decimals_price, min_amount, max_amount)}
        # ask format (sat2kas): {ceil(price): (2_decimals_price, min_amount, max_amount)}
        spread = 2
        min_amount = 10
        max_amount = 100
        if bid := kasbtc_ticker.get('bid'):
            bid = bid * 1e8
            self.price_offers['kas2sat'] = {
                floor(bid - spread): (round(bid - spread, 2), min_amount, max_amount)
            }
        if ask := kasbtc_ticker.get('ask'):
            ask = ask * 1e8
            self.price_offers['sat2kas'] = {
                ceil(ask + spread): (round(ask + spread, 2), min_amount, max_amount)
            }
        logger.debug(self.price_offers)


if __name__ == '__main__':
    maker = DynamicMaker(output_address='kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q')
    # alternatively, you can run a standard maker without automatic offers update
    # and update offers using the internal api (disabled by default) with an external script
    # e.g.:
    # maker = Maker(
    #     output_address='kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q',
    #     apiport=58080
    # )
    # then post offers to 127.0.0.1:58080/update_offers from another script,
    # check rows 54-55 for offers format:
    # import requests
    # import json
    # offers = {
    #     'sat2kas': {100: [100.50, 50, 100]},
    #     'kas2sat': {95: [94.50, 50, 100]}
    # }
    # res = requests.post('http://127.0.0.1:58080/update_offers', data=json.dumps(offers))
    # res.text will contain a string json with the updated offers, or an error
    # note: update_offers endpoint will overwrite previous offers

    # now start the p2p node, this will automatically take care of starting the maker swapper
    loop = asyncio.get_event_loop()
    p2p_maker = MakerNode(loop, maker)
    loop.run_until_complete(p2p_maker.start())

    # if you want to run a maker without p2p you can directly start the swapper:
    # loop.run_until_complete(maker.start())
