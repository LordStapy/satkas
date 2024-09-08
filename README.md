# SatKas
___

**WARNING: experimental code, use with caution!**  

SatKas is a proof-of-concept that enables atomic swaps between Lightning BTC and Kaspa.

### Requirements
#### Mandatory
> kaspad
> 
> kaspactl
> 
> Tor
#### Optional
> lnd/lncli
> 
> kaspawallet

Optional requirements are needed for the `taker_dummy_ui.py` script and to enhance swap speed.

**Hint:** Use a dedicated wallet for testing, there's a dedicated entry in the config file. 

### How to use
```
git clone https://github.com/LordStapy/satkas.git
cd satkas
# start a virtual environment
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
cp example.env .env
```
Edit `.env` then use one of the provided scripts to test the swap functionality:
#### manual_swap.py
Perform an atomic swap with known parameters, for testing or recovering swaps after a crash.
#### taker_quick_swap.py
Direct connection to a maker endpoint, the script automatically selects the best offer.
You can specify an endpoint when running the script, if not it will be asked.  
Run the script, then follow instructions.
```
python3 taker_quick_swap.py [maker-endpoint]
...
```
#### taker_dummy_ui.py
Dummy UI made with _curses_, a basic terminal UI library already available on python for Linux.  
Automatically join a basic p2p network and sync the orderbook with offers from all makers, select a price to see 
all detailed offers, upon selecting an offer you will be prompted to input the amount and then confirm the swap.
```
python3 taker_dummy_ui.py
...
```