## 🔄 BTC-KAS Atomic Swap Platform
### *Bridging Bitcoin and Kaspa Without Boundaries*

[![Status](https://img.shields.io/badge/Status-Proof%20of%20Concept-yellow)](https://github.com/LordStapy/satkas)
[![Network](https://img.shields.io/badge/Network-Live%20on%20Mainnet-green)](#)
[![License](https://img.shields.io/badge/License-Open%20Source-blue)](#)

---

### What are Atomic Swaps?

Atomic swaps are "digital agreement" that ensures both parties get what they agreed to, or no one gets anything.

The process is **fully trustless**: through the complete process, users don't need to trust anyone.

### Getting Started:
**Requirements:** 
- `tor` is required 
- a personal `kaspad` node is highly recommended.
- any LN wallet for receiving sats. For sending payments, make sure the wallet displays the payment preimage. (Makers currently need LND/lncli)
- any Kaspa wallet. (Makers currently need Golang kaspawallet)

Directly install from GitHub:

    # It's recommended to use a virtual environment
    python3 -m venv venv
    source venv/bin/activate
    # Install with pip
    # PyPi release is not currently available, the UI PoC was build with a dev version of kivyMD
    pip3 install git+https://github.com/LordStapy/satkas

Or, download and install from source:

    git clone https://github.com/LordStapy/satkas.git
    cd satkas
    # start a virtual environment
    python3 -m venv venv
    source venv/bin/activate
    pip3 install .

#### Start the UI app:

    satkas

**SatKas is experimental software, test it with small amounts of KAS and sats!**

Alternatively, the `examples` directory contains few scripts showing how to use the libraries, including running a Maker or a Taker from the command line.
You will need to copy the `example.env` file to `.env` and adjust the parameters to your needs.

Note: running a Maker requires extra dependencies and some coding skills, I don't recommend it at this stage. 

### How It Works
Here is a simplified schema of how the exchange happens:

```mermaid
sequenceDiagram
    participant U as 🧑 User (Taker)
    participant M as 🏪 Market Maker (Maker)
    
    Note over U: User has BTC (Lightning Network)
    Note over M: Market Maker has KAS

    Note over U,M: Discovery & Negotiation
    U->>M: Request BTC → KAS exchange offer
    M->>U: Reply with exchange rate offer
    U->>M: Accept offer + provide KAS receiving address

    Note over M: Lightning Invoice
    M->>M: Create Lightning Network invoice
    M->>U: 5. Send Lightning Network invoice

    Note over M: Kaspa Contract Lock
    M->>M: Create contract locking KAS with secret code

    Note over U: Payment & Receipt
    U->>U: Pay Lightning invoice and reveal secret

    Note over M: Market Maker receives BTC (Lightning Network)

    Note over U: Unlock KAS & Complete swap
    U->>U: Use revealed secret to unlock KAS from contract and send it to it's own wallet
        
    Note over U: User receives KAS

    Note over U,M: ✅ Exchange Complete!

```

Swaps work in **both directions**, users can exchange:
  - Bitcoin (LN) ---> KAS 
  - KAS ---> Bitcoin (LN)

### Contribute:

#### **Provide Feedback**
- What features do you need most?
- What concerns do you have?
- How would you use this platform?

#### **Test the Platform**
- Try the current proof of concept
- Report bugs and issues
- Share your experience

#### **Vote on Features**
- Help prioritize development
- Suggest new capabilities
- Shape the roadmap

---

Questions? Feedback? Criticisms? Suggestions? Tag me (@lordstapy) in Kaspa Official Discord server (votes-and-funding-discussions channel - https://discord.com/channels/599153230659846165/1032411383347826748)
