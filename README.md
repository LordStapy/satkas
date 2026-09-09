## 🔄 SatKas - a BTC-KAS Atomic Swap Platform

### *Bridging Bitcoin and Kaspa Without Boundaries*

---

### Getting Started:

**Requirements:** 

- `tor` is required 
- any LN wallet for receiving sats. **For sending payments, make sure the wallet displays the payment preimage**.
- any Kaspa / Bitcoin wallet can be used. One-click payments are available for go-kaspawallet and bitcoind users.



**Install:**

Note: kivymd has a dependency on the [cairo graphics library](https://cairographics.org/), SatKas doesn't use widgets requiring cairo rendering and we can bypass it with the following commands:

```
# It's recommended to use a virtual environment
python3 -m venv venv
source venv/bin/activate
# Install kivymd without dependencies
pip3 install kivymd==2.0.0 --no-deps
pip3 install satkas
# The error complaining about the missing materialshapes is expected, it's the cairo one we bypassed
```

If you have cairo installed, you can directly run:

```
pip3 install 'satkas[kivymd]'
```



#### Start the UI app:

```
satkas
```

**SatKas is experimental software, test it with small amounts of KAS and sats!**

Missing / limitations:

- Example scripts were not updated, they are most likely broken.
- P2P layer is not exposed to UI yet, makers are hardcoded.
- UI is Taker only, Maker interface TBD.
- DB is NOT encrypted, all configs (including passwords) are stored in plaintext.

Swaps work in **both directions**, users can exchange:

- Bitcoin (on-chain or LN) ---> KAS 
- KAS ---> Bitcoin (on-chain or LN)

---

For feedback and suggestions: tag me in Kaspa Official Discord server or drop a message on Telegram (@LordStapy)