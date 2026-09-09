"""Exceptions raised by the swapper core.

These live outside counterparty.py and atomic_swap.py so that frontends (UI,
CLI, and later a web backend) can handle swap failures without importing the
swapper classes themselves.
"""


class SwapError(Exception):
    """Base class for swap failures a caller may want to handle."""


class SwapRejected(SwapError):
    """The counterparty refused to open the swap."""


class ContractMismatch(SwapError):
    """The contract we generated differs from the one the counterparty sent."""


class TipUnavailable(SwapError):
    """A chain tip could not be read, so nothing time-based can be decided.

    Deliberately not an AssertionError: the maker's monitors catch those to
    mark a swap expired, and an unreachable node is not an expiry.
    """


class LocktimeRejected(SwapError, AssertionError):
    """A locktime is outside the range we are willing to accept.

    Subclasses AssertionError because the maker's monitors catch
    AssertionError from the locktime checks to mark a swap expired.
    """


# ExternalWalletRequired is raised by the services, so it lives in
# satkas.core.services.base_service. It is not re-exported here: importing any
# submodule of satkas.core.services runs that package's __init__, which pulls in
# ServiceManager and every service with it.


class PreimageInvalid(SwapError):
    """The supplied preimage does not hash to the swap's secret hash."""


class SwapExpired(SwapError):
    """The locktime elapsed before the swap completed."""


class InvoiceInvalid(SwapError):
    """A bolt11 invoice could not be decoded."""


class InteractionRequired(SwapError):
    """The swap needs something only a human can supply, and no frontend
    volunteered to ask for it.

    Raised by the SwapInteraction defaults rather than by a swap step: a run
    with internal wallets never reaches them.
    """


class KeySpaceMismatch(SwapError):
    """This side's address counter sits above its own count of swap rows.

    A swap's derivation index is its row's position among its side's rows, so
    a counter ahead of that count means indices were issued under the older
    scheme that counted both sides. Starting anyway would re-issue a key that
    has already been handed to a counterparty.
    """
