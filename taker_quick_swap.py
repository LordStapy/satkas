
import asyncio
import sys
import time

from dotenv import load_dotenv

from swapper.taker import Taker

load_dotenv()

if len(sys.argv) > 1:
    endpoint = sys.argv[1]
else:
    endpoint = input('\nInsert maker endpoint, leave empty for default: ').strip()

if not endpoint:
    endpoint = 'http://exlg6u3252bnzit7mgia3tpb2yctafmo3wbev72pwmxeqg7jiywsx2yd.onion'

# check endpoint params:
if not endpoint.startswith('http'):
    if endpoint.endswith('.onion'):
        endpoint = f"http://{endpoint}"
    else:
        endpoint = f"https://{endpoint}"

output_address = input('\nInsert kaspa address for redeem/refund of swap: ')
if not output_address.startswith('kaspa'):
    print('Wrong address?')
    sys.exit(1)

taker = Taker(output_address=output_address)
taker.maker_endpoint = endpoint

swap_type = input('\n[1] sat2kas\n[2] kas2sat\nSelect swap type [1/2]: ')
if swap_type == '1':
    swap_type = 'sat2kas'
elif swap_type == '2':
    swap_type = 'kas2sat'
else:
    sys.exit()

loop = asyncio.get_event_loop()
offers, valid_until = loop.run_until_complete(taker.query_price(swap_type))

best_offer_key = sorted(offers,
                        key=lambda x: offers[x][0],
                        reverse=(True if swap_type == 'kas2sat' else False)
                        )[0]
best_offer = offers[best_offer_key]
price, min_amt, max_amt = best_offer
print(f"\nBest offer:\nPrice: {price}, min amount: {min_amt}, max amount: {max_amt}")
print(f"Valid for {int(valid_until - time.time())} seconds")

kas_amount = input(f"Insert amount of KAS to swap (default {min_amt}): ")
if kas_amount == '':
    kas_amount = min_amt
else:
    kas_amount = int(kas_amount)
    if not (min_amt <= kas_amount <= max_amt):
        print(f"Wrong amount, should be {min_amt} < amount < {max_amt}")
        sys.exit(1)
if swap_type == 'sat2kas':
    print(f"\nSwapping {int(kas_amount * price)} sats for {kas_amount} KAS")
else:
    print(f"\nSwapping {kas_amount} KAS for {int(kas_amount * price)} sats")

confirm_swap = input(f"Confirm swap? [Y/n]: ")
if confirm_swap == '' or confirm_swap.lower() == 'y':
    if valid_until < time.time():
        print('Offer expired, exiting...')
        sys.exit()
    if swap_type == 'sat2kas':
        result = loop.run_until_complete(taker.sat2kas(kas_amount=kas_amount, price=price))
    else:
        result = loop.run_until_complete(taker.kas2sat(kas_amount=kas_amount, price=price))
    print(f"\nSwap result: {result}")
else:
    print('Goodbye!')
