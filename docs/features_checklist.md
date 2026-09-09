
# Planned features overview 

Brief description of the proposed features.

## TIER 0
#### API
- [ ] **extended REST API for interacting with swapping and p2p nodes**

	A full set of endpoints for managing the nodes with external tools, e.g. fetch orderbook, start/manage swaps, 
connect to p2p nodes, etc.
    This can be used by external tools/scripts or for attaching the GUI to an already running daemon.
- [ ] **documentation on endpoints**

	Complete API specification for all endpoints, including payload and response format.
#### Daemon and Desktop GUI
- [ ] **Kivy based UI**

	Kivy is a python GUI framework based on widgets.
- [ ] **start with GUI or daemon only**

	satkas can be launched as a daemon/service, GUI can either attach to a running daemon (via API) or spawn an internal daemon automatically. 
- [ ] **Taker and Maker interfaces**

	- orderbook page: takers can select offers from the p2p orderbook.
    - direct swap: taker can directly connect to  known makers.
    - Makers can see/update/delete their offers.
- [ ] **settings manager, stored in DB (first run wizard, both on CLI and GUI)**

	Settings stored in a database, no more .env config file. 

	A configuration wizard will help the user configure the software when it's first started (either via GUI or command line).

#### Lightning
- [ ] **LNBITS: allows using a wide range of Lightning nodes**

	https://github.com/lnbits/lnbits

	LNbits is a middleman that can interact with many Lightning nodes with a unified API. 
	By using LNbits makers (and optionally takers) can use their favourite LN node as a funding source, even custodial ones (definetely not recommended), check the complete list at https://github.com/lnbits/lnbits/blob/main/docs/guide/funding-sources-table.md 
    
    **BEWARE: the backend must provide the payment preimage when a payment is completed.**
- [ ] **sanity checks on LN fees**

	lightning fees are not deterministic, this may be exploited by a bad actor (not easy, yet possible). A sanity check will avoid unwanted fees for makers (and automated takers).

#### Kaspa wallet
- [ ] **replace kaspawallet (Go) with RK (or other) wallet**

	Adding support for RustyKaspa native wallet will reduce external dependencies.
	
	kaspawallet can be kept for backward compatibility.
- [ ] **fee manager**

	Currently, miner fees are hardcoded for redeems and refunds, a fee manager will ensure proper fees. 

#### Android mobile app
- [ ] **Kivy based app**

	The framework used for the desktop GUI can be used to export native mobile apps.
	There is a catch, due to internal working of Kivy, the exported mobile app will require more storage than classic apps. 
- [ ] **Taker-only Android app apk**

	Mobile app will NOT have a maker interface (at least not in the current proposal, maybe in a future update).

	The proposal covers building instructions and pre-compiled apk.

	Optional: publish app on F-Droid and Play Store (the latter may be hard, Google changed store policies). 

#### P2P
- [ ] **store p2p nodes info for re-use**

	Known nodes are stored in db.
- [ ] **reduce dependency on seed entry points**

	Seeders will be needed only on the first run, afterward a node can connect to already discovered nodes.
- [ ] **timeout/connection drop handling**

	Tor connections are not 100% reliable, sometimes the connection is lost or frozen, a better mechanism for managing nodes will be developed.
- [ ] **min/max connections handler**

	User specified minimum and maximum number of inbound/outbound nodes (relevant for makers, takers can only make outbound connections).
#### Makers
- [ ] **anti-spam protection**

  Proposal: when the protection activates, a non-refundable upfront-payment is requested.

  A bad taker can spam a maker by requesting many sat2kas swaps, without honoring the swap.
  Few ideas were explored to enable a spam protection after a certain number of refunded swaps (user configurable), 
  from proof-of-work to pre-payment. 
  Proposal covers a maker-defined, non-refundable, payment e.g. 1 KAS, if spam protection activates, taker is required to pay an advance-fee (a good recurring taker may be allowed to swap without fee, see scoring system section).

#### On-chain swaps
- [ ] **swap logic (scripts/addresses/signatures)**

	KAS and BTC scripting, p2sh address derivation and redeem/refund transaction generation (KAS side will be heavily reused from LN-KAS swaps).
- [ ] **taker/maker interaction**

	P2P orderbook for on-chain swaps. 
- [ ] **on-chain monitoring**

	On-chain swaps require monitoring Kaspa DAG and Bitcoin blockchain. Additionally, a fallback mechanism using explorers will be developed.

#### User experience
- [ ] **Recover ongoing swap if app is closed**

    If satkas is closed during a swap (or crashes), the swap can be recovered when the software is restarted.
- [ ] **Pre-compiled binaries**

	Research needed, pyInstaller and PyPi are the top candidates (at least one is included in the proposal).
- [ ] **docker images and docker-compose examples**

	Docker images will be made available, with examples of both light and full docker-compose files.


## TIER 1
Tier 1 includes everything listed under Tier 0.

#### Localizations
- [ ] **multi-language support**

	Translations will be made using LLMs.

#### Makers utilities
- [ ] **balance checker**

	Monitor both KAS and BTC balances, automatically removes offers from orderbook if balance is too low.
- [ ] **CEX tracker**

	Plugin for makers that monitors a selected CEX price and automatically updates orderbook offers with a user defined spread.
- [ ] **one-time offers**

    Automatically remove offers after a swap occurred (by default offers never expires).

    Makers will be able to post one-time offers in the orderbook, after a swap the offer will be removed (or updated if not completely filled).
- [ ] **Telegram Bot notifications**

	Makers can set up a personal telegram bot that will notify in real-time of ongoing swaps and their status (live, completed, refunded).
	
	Research: this can be later extended to allow more complex tasks, e.g. remote control of maker nodes.

#### P2P
- [ ] **clearnet support for p2p (opt-in, disabled by default)**

	Advanced users will be allowed to run the p2p layer over clearnet (both makers and takers).

#### Stats and reports
- [ ] **internal stats**

	Users can check their nodes stats, both makers and takers, with reports on activity (number of swaps, volumes, etc). 
- [ ] **swaps monitoring and global stats (hosted by me, open-sourced)**

	Atomic swaps use a commit/reveal transactions pair and have a distinct signature-script (privacy is preserved, a monitoring entity can only detect that a swap redeem/refund occurred).

	This tool will track global statistics about swaps number, swaps volume and redeem/refund ratio.
- [ ] **internal score system**

    Successful swaps increase the peer score, refunds decrease it. 

    Takers can filter/sort known makers based on trade history (more successful swaps volume -> better maker). 

	Makers can offer perks/discounts/lower-spreads to good takers or refuse swaps from low score peers (this can be paired with the antispam system).
	

#### Internal wallet (research) 
**NOTE: I'm not including the development of the following features in the proposal, as I'm not even sure they are feasible. I will research this topic though, and if this is doable, will be deployed as experimental feature and disabled by default.**

By having a (minimal) internal wallet we can extend functionality of the platform.
- [ ] **KIP10 for antispam protection**

	Since pre-payment is not refundable, an attacker-maker can steal the fee, with kip10 we can have the taker pay as little as few thousands sompi/dwork, removing the incentive for an attack.
- [ ] **fidelity bonds**

	By locking a certain amount of KAS for an extended time, users (especially makers) can show to the network that they are not bad actors or spammers -> enhanced scoring system. 
