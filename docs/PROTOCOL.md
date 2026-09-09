### How It Works

Here is a simplified schema of how the LN exchange happens:

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



On-chain swaps (BTC ↔ KAS) skip Lightning. Negotiation is omitted below: the quote is already accepted.

The same sequence is used in both directions; the taker always locks first.

```mermaid
sequenceDiagram
    participant U as 🧑 User (Taker)
    participant M as 🏪 Market Maker (Maker)

    Note over U: Taker picks a secret
    U->>M: Hash of the secret

    Note over U: Taker locks first (timelock T)
    U->>U: Fund contract with hashlock + timelock T

    Note over M: Maker waits for taker funding
    M->>M: Fund contract with the same hashlock + timelock T2 (T2 < T)

    Note over U: Taker redeems maker's contract
    U->>U: Spend with the secret (secret is now on-chain)

    Note over M: Maker watches that redeem
    M->>M: Reuse the revealed secret to spend taker's contract

    Note over U,M: ✅ Exchange Complete!
```



If the taker never redeems, T2 expires first so the maker refunds, then T expires so the taker refunds. T2 must be shorter than T: otherwise the taker could refund their own lock at T and still redeem the maker's lock with the secret.

### Notes

- Both parties derive each contract address from the same public parameters (secret hash, keys, timelocks). Neither side needs to trust an address the other sends.
- Maker quotes include a base rate and network fees. Makers provide liquidity; takers pay the fees.
- On-chain only: the taker chooses the secret and never sends it. The maker only learns it from the taker's redeem transaction.
- On-chain only: the maker does not fund until the taker's lock is seen on-chain. If the taker never funds, the maker never locks coins.

