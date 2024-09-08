
import asyncio
import sys
import time
import math
import curses
import logging
logging.basicConfig(filename='taker_dummy_ui.log', filemode='w', level=logging.DEBUG)

from dotenv import load_dotenv

from swapper.taker import Taker
from p2p.taker_p2p_node import TakerNode

load_dotenv()

if len(sys.argv) == 1:
    output_address = input('Insert a kaspa address for the redeem/refund of swaps: ').strip()
else:
    output_address = sys.argv[1]
if output_address and not output_address.startswith('kaspa'):
    print('Wrong address?')
    sys.exit(1)

if not output_address:
    # default to test address if left empty
    output_address = 'kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q'

taker1 = Taker(output_address=output_address)
loop = asyncio.get_event_loop()
node1 = TakerNode(loop, taker1)


async def draw_amount_selection_page(stdscr, swap_type, offer):
    curses.nocbreak()
    curses.echo()
    stdscr.nodelay(False)
    stdscr.clear()
    stdscr.addstr(1, 3, swap_type)
    stdscr.addstr(2, 1, 'Input swap amount (in KAS): ')
    stdscr.refresh()
    amount = int(stdscr.getstr().decode().strip())
    curses.noecho()
    curses.cbreak()
    stdscr.nodelay(True)
    price, min_amt, max_amt, endpoint = offer
    logging.info(f"Price: {price}, min-max: {min_amt}-{max_amt}, endpoint: {endpoint}")
    price = float(price)
    p2p_price = math.floor(price) if swap_type == 'kas2sat' else math.ceil(price)
    res, valid_until = await node1.swapnode.query_price(swap_type, kas_amount=0, p2p_price=p2p_price, endpoint=endpoint)
    logging.info(res)
    node1.swapnode.maker_endpoint = endpoint
    x = stdscr.getch()
    swap_result = None
    while x != 27:
        stdscr.clear()
        stdscr.addstr(1, 3, swap_type)
        if swap_type == 'sat2kas':
            swap_info_text = f"Swapping {int(amount * res[0])} sats for {amount} KAS"
        else:
            swap_info_text = f"Swapping {amount} KAS for {int(amount * res[0])} sats"
        stdscr.addstr(2, 1, swap_info_text)
        if swap_result is None:
            stdscr.addstr(3, 1, f"Offer valid for {int(valid_until - time.time())} seconds")
            stdscr.addstr(5, 1, "Press Enter to confirm swap, R to refresh, ESC to go back")
        else:
            stdscr.addstr(3, 1, 'Offer accepted')
            stdscr.addstr(5, 1, ' ' * 60)
            stdscr.addstr(5, 1, 'Press ESC to go back')
            swap_result = 'completed!' if swap_result == True else swap_result
            stdscr.addstr(9, 1, f"Swap completed/failed: {swap_result}")
            stdscr.addstr(10, 1, 'Check logs for more details')
        stdscr.refresh()
        if x == ord('\n'):
            stdscr.addstr(3, 1, 'Offer accepted' + ' ' * 20)
            stdscr.addstr(5, 1, ' ' * 60)
            stdscr.addstr(7, 1, f"Swap started! Please wait...")
            stdscr.refresh()
            if swap_type == 'sat2kas':
                swap_result = await node1.swapnode.sat2kas(kas_amount=amount, price=res[0])
            else:
                swap_result = await node1.swapnode.kas2sat(kas_amount=amount, price=res[0])
            valid_until = 0
        elif x == ord('r'):
            res, valid_until = await node1.swapnode.query_price(swap_type, kas_amount=0, p2p_price=p2p_price, endpoint=endpoint)
        elif x == curses.ERR:
            await asyncio.sleep(0.1)

        if valid_until and time.time() > valid_until:
            break
        x = stdscr.getch()


async def draw_orderbook_page(stdscr, bids, asks, selected_position, selected_box, bid_box, ask_box, max_row):
    stdscr.clear()

    selected_text = curses.color_pair(1)
    normal_text = curses.A_NORMAL

    row_num = min(len(bids), len(asks), max_row)

    bid_box.clear()
    ask_box.clear()
    bid_box.box()
    ask_box.box()

    stdscr.addstr(1, 15, 'BID')
    stdscr.addstr(1, 47, 'ASK')
    stdscr.addstr(2, 5, 'price  amount (min-max)')
    stdscr.addstr(2, 37, 'price  amount (min-max)')

    for i, (bid, ask) in enumerate(zip(bids, asks), start=1):
        if i == selected_position:
            if selected_box == 1:
                bid_box.addstr(i, 2, f"{bid}", selected_text)
                ask_box.addstr(i, 2, f"{ask}", normal_text)
            elif selected_box == 2:
                bid_box.addstr(i, 2, f"{bid}", normal_text)
                ask_box.addstr(i, 2, f"{ask}", selected_text)

        else:
            bid_box.addstr(i, 2, f"{bid}", normal_text)
            ask_box.addstr(i, 2, f"{ask}", normal_text)
        if i == row_num:
            break

    stdscr.addstr(15, 1, 'Up/Down/Left/Right: move selection')
    stdscr.addstr(16, 1, 'R: refresh orderbook')
    stdscr.addstr(17, 1, 'Enter: select orderbook entry')
    stdscr.addstr(18, 1, 'Esc: exit')

    stdscr.refresh()
    bid_box.refresh()
    ask_box.refresh()


async def load_offers(swap_type, p2p_price):
    servers = node1.orderbook[swap_type][p2p_price]
    endpoints = [s['payload']['onion'] for s in servers]
    logging.info(f"endpoints: {endpoints}")

    tasks = [node1.swapnode.query_price(swap_type, 0, p2p_price, endpoint) for endpoint in endpoints]
    results = await asyncio.gather(*tasks)

    offers = []
    for endpoint, (res, valid_until) in zip(endpoints, results):
        # if len(endpoint) > 25:
        #     endpoint = f"{endpoint[:7]}...{endpoint[-13:]}"
        # offers.append(f"{res[0]:.2f} {res[1]:>6d} {res[2]:>7d}        {endpoint}")
        offers.append((res[0], res[1], res[2], endpoint))
    return offers


async def offers_screen(stdscr, swap_type, p2p_price):
    stdscr.clear()

    selected_text = curses.color_pair(1)
    normal_text = curses.A_NORMAL

    stdscr.addstr(5, 5, f"Loading offers for {p2p_price}")
    stdscr.refresh()

    offers = await load_offers(swap_type, p2p_price)
    stdscr.clear()
    stdscr.refresh()

    offers_box = curses.newwin(len(offers) + 2, 60, 3, 1)
    offers_box.box()

    selected_position = 1

    stdscr.addstr(1, 10, swap_type)
    stdscr.addstr(2, 3, 'price    min-max amount      endpoint')
    for i, offer in enumerate(offers, start=1):
        price, min_amt, max_amt, endpoint = offer
        if len(endpoint) > 25:
            endpoint = f"{endpoint[:7]}...{endpoint[-13:]}"
        if i == selected_position:
            offers_box.addstr(i, 2, f"{price:.2f} {min_amt:>6d} {max_amt:>7d}        {endpoint}", selected_text)
        else:
            offers_box.addstr(i, 2, f"{price:.2f} {min_amt:>6d} {max_amt:>7d}        {endpoint}", normal_text)

    stdscr.refresh()
    offers_box.refresh()

    x = stdscr.getch()
    while x != 27:
        if x == curses.KEY_DOWN:
            if selected_position + 1 <= len(offers):
                selected_position += 1
        elif x == curses.KEY_UP:
            if selected_position - 1 >= 1:
                selected_position -= 1
        elif x == ord('\n'):
            await draw_amount_selection_page(stdscr, swap_type, offers[selected_position - 1])
            return
        elif x == ord('r'):
            offers = await load_offers(swap_type, p2p_price)
        elif x == curses.ERR:
            await asyncio.sleep(0.1)

        for i, offer in enumerate(offers, start=1):
            price, min_amt, max_amt, endpoint = offer
            if len(endpoint) > 25:
                endpoint = f"{endpoint[:7]}...{endpoint[-13:]}"
            if i == selected_position:
                offers_box.addstr(i, 2, f"{price:.2f} {min_amt:>6d} {max_amt:>7d}        {endpoint}", selected_text)
            else:
                offers_box.addstr(i, 2, f"{price:.2f} {min_amt:>6d} {max_amt:>7d}        {endpoint}", normal_text)

        stdscr.addstr(15, 1, 'Up/Down: move selection')
        stdscr.addstr(16, 1, 'R: refresh offers')
        stdscr.addstr(17, 1, 'Enter: select offer')
        stdscr.addstr(18, 1, 'Esc: back to orderbook')

        stdscr.refresh()
        offers_box.refresh()
        x = stdscr.getch()


async def orderbook_screen(stdscr):
    stdscr.clear()

    bids, asks = await node1.render_orderbook(return_bidask=True)

    max_row = 8

    bid_box = curses.newwin(max_row + 2, 30, 3, 1)
    ask_box = curses.newwin(max_row + 2, 30, 3, 33)

    row_num = min(len(bids), len(asks), max_row)

    selected_position = 1
    selected_box = 1

    await draw_orderbook_page(stdscr, bids, asks, selected_position, selected_box, bid_box, ask_box, max_row)

    refresh_counter = 0

    x = stdscr.getch()
    while x != 27:
        if x == curses.KEY_DOWN:
            if selected_position + 1 <= row_num:
                selected_position += 1
        elif x == curses.KEY_UP:
            if selected_position - 1 >= 1:
                selected_position -= 1
        elif x == curses.KEY_RIGHT:
            if selected_box == 1:
                selected_box = 2
        elif x == curses.KEY_LEFT:
            if selected_box == 2:
                selected_box = 1
        elif x == ord('\n'):
            offer = bids[selected_position - 1] if selected_box == 1 else asks[selected_position - 1]
            p2p_price = int(offer.strip().split()[0])
            swap_type = 'sat2kas' if selected_box == 2 else 'kas2sat'
            await offers_screen(stdscr, swap_type, p2p_price)
            stdscr.clear()
            bids, asks = await node1.render_orderbook(return_bidask=True)
            row_num = min(len(bids), len(asks), max_row)
        elif x == ord('r'):
            bids, asks = await node1.render_orderbook(return_bidask=True)
            row_num = min(len(bids), len(asks), max_row)
        elif x == curses.ERR:
            refresh_counter += 1
            if refresh_counter == 50:
                bids, asks = await node1.render_orderbook(return_bidask=True)
                row_num = min(len(bids), len(asks), max_row)
                refresh_counter = 0
            await asyncio.sleep(0.1)

        await draw_orderbook_page(stdscr, bids, asks, selected_position, selected_box, bid_box, ask_box, max_row)

        x = stdscr.getch()


async def main():
    stdscr = curses.initscr()
    curses.noecho()
    curses.curs_set(False)
    curses.cbreak()
    stdscr.keypad(True)
    stdscr.nodelay(True)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)

    bids, asks = None, None

    while not bids or not asks:
        stdscr.addstr(5, 5, f"Waiting for orderbook offers")
        stdscr.refresh()
        await asyncio.sleep(1)
        bids, asks = await node1.render_orderbook(return_bidask=True)

    await orderbook_screen(stdscr)

    curses.endwin()


if __name__ == '__main__':
    loop.create_task(node1.status_check(infinite_loop=True))
    loop.run_until_complete(main())

