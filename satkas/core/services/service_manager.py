# ServiceManager class that manages all services

import asyncio
from satkas.core.db.models import Setting
from .tor_service import TorService
from .kaspad_service import KaspadService
from .kaspa_explorer_service import KaspaExplorerService
from .kaspawallet_service import KaspawalletService
#from .rustykaspawallet_service import RustyKaspaWalletService
from .external_kaspawallet_service import ExternalKaspaWalletService
from .internal_kaspawallet_service import InternalKaspaWalletService
from .lnbits_service import LNBitsService
from .lncli_service import LncliService
from .external_ln_wallet_service import ExternalLNWalletService
from .bitcoind_service import BitcoindService
from .mempool_service import MempoolService
from .bitcoind_wallet_service import BitcoindWalletService
from .external_btc_wallet_service import ExternalBtcWalletService


class ServiceManager:
    """
    ServiceManager handles all services in the application.
    It manages multiple service implementations and ensures only one is used
    when multiple flavors are available (e.g., kaspa wallet services).
    """

    def __init__(self):
        # Service instances
        self._tor_service = None
        self._kaspad_service = None
        self._kaspa_wallet_service = None
        self._ln_wallet_service = None
        self._bitcoin_service = None
        self._btc_wallet_service = None
        # Public explorers kept aside for broadcast fallback, built on demand.
        self._fallback_broadcaster_kas = None
        self._fallback_broadcaster_btc = None

        self.options = {
            'kaspa_wallet': ['go', 'external', 'internal'],  # 'rusty' disabled: untested
            'ln_wallet': ['lnbits', 'lncli', 'external'],
            'kaspa_monitor': ['kaspad', 'explorer'],
            'bitcoin_monitor': ['bitcoind', 'mempool'],
            'btc_wallet': ['bitcoind', 'external'],
        }
        # Preferred wallet implementation (can be configured)
        # Load from database with fallback to 'external'
        self._preferred_wallet = Setting.get_value('app.preferred_kaspa_wallet', 'external')
        self._preferred_ln_wallet = Setting.get_value('app.preferred_ln_wallet', 'external')
        self._preferred_kaspa_monitor = Setting.get_value('app.preferred_kaspa_monitor', 'kaspad')
        self._preferred_bitcoin_monitor = Setting.get_value('app.preferred_bitcoin_monitor', 'bitcoind')
        self._preferred_btc_wallet = Setting.get_value('app.preferred_btc_wallet', 'external')

        # List of callables to be notified when services change
        self._observers = []

    # Observer management
    def register_observer(self, callback):
        if callback and callback not in self._observers:
            self._observers.append(callback)

    def unregister_observer(self, callback):
        if callback in self._observers:
            self._observers.remove(callback)

    def _notify_change(self, changed_services=None):
        services = changed_services if changed_services else []
        # Copy to avoid modification during iteration
        for observer in list(self._observers):
            try:
                observer(services)
            except Exception:
                # Best-effort notify; ignore observer exceptions
                pass

    # Service properties
    @property
    def tor_service(self):
        if self._tor_service is None:
            self._tor_service = TorService()
            self._notify_change(['tor_service'])
        return self._tor_service

    @property
    def kaspad_service(self):
        if self._kaspad_service is None:
            self._kaspad_service = self._select_kaspad_service()
            self._notify_change(['kaspad_service'])
        return self._kaspad_service

    @property
    def kaspa_wallet_service(self):
        if self._kaspa_wallet_service is None:
            self._kaspa_wallet_service = self._select_kaspa_wallet_service()
            self._notify_change(['kaspa_wallet_service'])
        return self._kaspa_wallet_service

    @property
    def ln_wallet_service(self):
        if self._ln_wallet_service is None:
            self._ln_wallet_service = self._select_ln_wallet_service()
            self._notify_change(['ln_wallet_service'])
        return self._ln_wallet_service

    @property
    def bitcoin_service(self):
        if self._bitcoin_service is None:
            self._bitcoin_service = self._select_bitcoin_service()
            self._notify_change(['bitcoin_service'])
        return self._bitcoin_service

    @property
    def btc_wallet_service(self):
        if self._btc_wallet_service is None:
            self._btc_wallet_service = self._select_btc_wallet_service()
            self._notify_change(['btc_wallet_service'])
        return self._btc_wallet_service

    # Wallet mode accessors
    #
    # For UI affordances only: choosing between a pay button and a QR code.
    # Never gate a spend on these - call the service and let it raise
    # ExternalWalletRequired, so the two can never disagree.
    def kas_wallet_is_external(self):
        return self._preferred_wallet == 'external'

    def ln_wallet_is_external(self):
        return self._preferred_ln_wallet == 'external'

    def btc_wallet_is_external(self):
        return self._preferred_btc_wallet == 'external'

    def _select_ln_wallet_service(self):
        """
        Select which LN wallet service to use.
        Flavors: lnbits, lncli, external.
        """

        services_to_try = []
        if self._preferred_ln_wallet == 'lnbits':
            services_to_try.append(LNBitsService)
        elif self._preferred_ln_wallet == 'lncli':
            services_to_try.append(LncliService)
        elif self._preferred_ln_wallet == 'external':
            services_to_try.append(ExternalLNWalletService)

        for service_class in services_to_try:
            try:
                service = service_class()
                # Run detection synchronously if possible, or just assume it's available
                # In a real implementation, you might want to await detection
                if hasattr(service, 'detect'):
                    # For now, just instantiate and let the service handle detection later
                    return service
            except Exception as e:
                print(f"Failed to initialize {service_class.__name__}: {e}")
                continue

        # fallback on external
        return ExternalLNWalletService()

    def _select_kaspa_wallet_service(self):
        """
        Select which kaspa wallet service to use based on availability and preference.
        Priority: preferred implementation if available, otherwise fallback to other.
        """
        services_to_try = []

        # Add services based on preference order
        # if self._preferred_wallet == 'rusty':
        #     services_to_try = [RustyKaspaWalletService, KaspawalletService]
        if self._preferred_wallet == 'go':
            services_to_try = [KaspawalletService]  #, RustyKaspaWalletService]
        elif self._preferred_wallet == 'external':
            services_to_try = [ExternalKaspaWalletService, KaspawalletService]  #, RustyKaspaWalletService]
        elif self._preferred_wallet == 'internal':
            services_to_try = [InternalKaspaWalletService]

        # Try to detect each service in order
        for service_class in services_to_try:
            try:
                service = service_class()
                # Run detection synchronously if possible, or just assume it's available
                # In a real implementation, you might want to await detection
                if hasattr(service, 'detect'):
                    # For now, just instantiate and let the service handle detection later
                    return service
            except Exception as e:
                print(f"Failed to initialize {service_class.__name__}: {e}")
                continue

        # Try ExternalKaspaWalletService as additional fallback (if not already tried)
        if ExternalKaspaWalletService not in services_to_try:
            try:
                print("Trying External Kaspa Wallet as fallback...")
                service = ExternalKaspaWalletService()
                return service
            except Exception as e:
                print(f"Failed to initialize ExternalKaspaWalletService: {e}")

    def _select_kaspad_service(self):
        """
        Select which Kaspa monitor service to use based on preference.
        Flavors: kaspad (node RPC) or explorer (REST). Property remains
        `kaspad_service` so callers stay flavor-agnostic.
        """
        services_to_try = []
        if self._preferred_kaspa_monitor == 'kaspad':
            services_to_try = [KaspadService, KaspaExplorerService]
        elif self._preferred_kaspa_monitor == 'explorer':
            services_to_try = [KaspaExplorerService, KaspadService]

        for service_class in services_to_try:
            try:
                return service_class()
            except Exception as e:
                print(f"Failed to initialize {service_class.__name__}: {e}")
                continue

        return KaspadService()

    def _select_bitcoin_service(self):
        """
        Select which Bitcoin monitor service to use based on preference.
        Flavors: bitcoind (watch-only RPC) or mempool (Esplora REST).
        """
        services_to_try = []
        if self._preferred_bitcoin_monitor == 'bitcoind':
            services_to_try = [BitcoindService, MempoolService]
        elif self._preferred_bitcoin_monitor == 'mempool':
            services_to_try = [MempoolService, BitcoindService]

        for service_class in services_to_try:
            try:
                return service_class()
            except Exception as e:
                print(f"Failed to initialize {service_class.__name__}: {e}")
                continue

        return BitcoindService()

    def fallback_broadcaster(self, chain):
        """A public explorer that can broadcast, whatever the preference is.

        The selectors above keep one flavor per slot, so when the configured
        node is the one that is down there is nothing else instantiated to try.
        This hands back the REST flavor for the chain - KaspaExplorerService or
        MempoolService - so a redeem or a refund has a second way out. Both
        match the signature of the service they stand in for
        (`submit_transaction` and `send_raw_transaction`).

        Returns None when it cannot be built, which callers treat as "no
        fallback available" rather than as an error.
        """
        attr = f"_fallback_broadcaster_{chain}"
        existing = getattr(self, attr, None)
        if existing is not None:
            return existing
        service_class = {
            'kas': KaspaExplorerService,
            'btc': MempoolService,
        }.get(chain)
        if service_class is None:
            return None
        # Reuse the configured service when it already is that flavor, so a
        # user on the explorer does not end up with two of them.
        for current in (self._kaspad_service, self._bitcoin_service):
            if isinstance(current, service_class):
                setattr(self, attr, current)
                return current
        try:
            service = service_class()
        except Exception as e:
            print(f"Failed to initialize fallback broadcaster {service_class.__name__}: {e}")
            return None
        setattr(self, attr, service)
        return service

    def _select_btc_wallet_service(self):
        """
        Select which BTC spending wallet service to use based on preference.
        Flavors: bitcoind (keys wallet RPC) or external.
        """
        services_to_try = []
        if self._preferred_btc_wallet == 'bitcoind':
            services_to_try = [BitcoindWalletService, ExternalBtcWalletService]
        elif self._preferred_btc_wallet == 'external':
            services_to_try = [ExternalBtcWalletService]

        for service_class in services_to_try:
            try:
                return service_class()
            except Exception as e:
                print(f"Failed to initialize {service_class.__name__}: {e}")
                continue

        return ExternalBtcWalletService()

    def set_preferred_kaspa_wallet(self, preference):
        """
        Set preferred kaspa wallet implementation.
        :param preference: 'go', 'external', or 'internal'  # 'rusty' disabled: untested
        """
        if preference not in ['go', 'external', 'internal']:  # 'rusty'
            raise ValueError("Preference must be 'go', 'external', or 'internal'")

        if preference != self._preferred_wallet:
            self._preferred_wallet = preference
            # Reset the current wallet service so it will be re-selected on next access
            if self._kaspa_wallet_service is not None:
                # Clean up current service if needed
                if hasattr(self._kaspa_wallet_service, 'disable'):
                    self._kaspa_wallet_service.disable()
                self._kaspa_wallet_service = None
                self._notify_change(['kaspa_wallet_service'])

    def set_preferred_ln_wallet(self, preference):
        """
        Set preferred LN wallet implementation.
        :param preference: 'lnbits', 'lncli', 'external'
        """
        if preference not in ['lnbits', 'lncli', 'external']:
            raise ValueError("Preference must be 'lnbits', 'lncli', or 'external'")

        if preference != self._preferred_ln_wallet:
            self._preferred_ln_wallet = preference
            # Reset the current LN wallet service so it will be re-selected on next access
            if self._ln_wallet_service is not None:
                # Clean up current service if needed
                if hasattr(self._ln_wallet_service, 'disable'):
                    self._ln_wallet_service.disable()
                self._ln_wallet_service = None
                self._notify_change(['ln_wallet_service'])

    def set_preferred_kaspa_monitor(self, preference):
        """
        Set preferred Kaspa monitor implementation.
        :param preference: 'kaspad' or 'explorer'
        """
        if preference not in ['kaspad', 'explorer']:
            raise ValueError("Preference must be 'kaspad' or 'explorer'")

        if preference != self._preferred_kaspa_monitor:
            self._preferred_kaspa_monitor = preference
            if self._kaspad_service is not None:
                if hasattr(self._kaspad_service, 'disable'):
                    self._kaspad_service.disable()
                self._kaspad_service = None
                self._notify_change(['kaspad_service'])

    def set_preferred_bitcoin_monitor(self, preference):
        """
        Set preferred Bitcoin monitor implementation.
        :param preference: 'bitcoind' or 'mempool'
        """
        if preference not in ['bitcoind', 'mempool']:
            raise ValueError("Preference must be 'bitcoind' or 'mempool'")

        if preference != self._preferred_bitcoin_monitor:
            self._preferred_bitcoin_monitor = preference
            if self._bitcoin_service is not None:
                if hasattr(self._bitcoin_service, 'disable'):
                    self._bitcoin_service.disable()
                self._bitcoin_service = None
                self._notify_change(['bitcoin_service'])

    def set_preferred_btc_wallet(self, preference):
        """
        Set preferred BTC spending wallet implementation.
        :param preference: 'bitcoind' or 'external'
        """
        if preference not in ['bitcoind', 'external']:
            raise ValueError("Preference must be 'bitcoind' or 'external'")

        if preference != self._preferred_btc_wallet:
            self._preferred_btc_wallet = preference
            if self._btc_wallet_service is not None:
                if hasattr(self._btc_wallet_service, 'disable'):
                    self._btc_wallet_service.disable()
                self._btc_wallet_service = None
                self._notify_change(['btc_wallet_service'])

    def initialize_services(self):
        """
        Initialize all services. This ensures services are created and ready.
        """
        # Access each service property to trigger initialization
        _ = self.tor_service
        _ = self.kaspad_service
        _ = self.kaspa_wallet_service
        _ = self.ln_wallet_service
        _ = self.bitcoin_service
        _ = self.btc_wallet_service

    async def detect_all_services(self):
        """
        Run detection on all services asynchronously.
        """
        tasks = []

        if self._tor_service:
            tasks.append(self._tor_service.detect())
        if self._kaspad_service:
            tasks.append(self._kaspad_service.detect())
        if self._kaspa_wallet_service:
            tasks.append(self._kaspa_wallet_service.detect())
        if self._ln_wallet_service:
            tasks.append(self._ln_wallet_service.detect())
        if self._bitcoin_service:
            tasks.append(self._bitcoin_service.detect())
        if self._btc_wallet_service:
            tasks.append(self._btc_wallet_service.detect())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def enable_all_services(self):
        """
        Enable all services that are detected.
        """
        if self._tor_service and self._tor_service.is_detected:
            self._tor_service.enable()
        if self._kaspad_service and self._kaspad_service.is_detected:
            self._kaspad_service.enable()
        if self._kaspa_wallet_service and self._kaspa_wallet_service.is_detected:
            if isinstance(self._kaspa_wallet_service, InternalKaspaWalletService):
                self._kaspa_wallet_service.enable(sm=self)
            else:
                self._kaspa_wallet_service.enable()
        if self._ln_wallet_service and self._ln_wallet_service.is_detected:
            self._ln_wallet_service.enable()
        if self._bitcoin_service and self._bitcoin_service.is_detected:
            self._bitcoin_service.enable()
        if self._btc_wallet_service and self._btc_wallet_service.is_detected:
            self._btc_wallet_service.enable()

    def disable_all_services(self):
        """
        Disable all services.
        """
        if self._tor_service:
            self._tor_service.disable()
        if self._kaspad_service:
            self._kaspad_service.disable()
        if self._kaspa_wallet_service:
            self._kaspa_wallet_service.disable()
        if self._ln_wallet_service:
            self._ln_wallet_service.disable()
        if self._bitcoin_service:
            self._bitcoin_service.disable()
        if self._btc_wallet_service:
            self._btc_wallet_service.disable()

    def reload_kaspa_wallet_config(self):
        """
        Reload the kaspa wallet service configuration.
        This allows the wallet to pick up new kaspad settings and reconnect if needed.
        """
        if self._kaspa_wallet_service:
            self._kaspa_wallet_service.reload_config()
            self._notify_change(['kaspa_wallet_service'])

    def save_wallet_preferences(self):
        """
        Save current wallet preferences to the database.
        This method handles persistence of wallet type preferences.
        """
        # Save wallet preferences
        Setting.set_value('app.preferred_kaspa_wallet', self._preferred_wallet, 'str')
        Setting.set_value('app.preferred_ln_wallet', self._preferred_ln_wallet, 'str')
        Setting.set_value('app.preferred_kaspa_monitor', self._preferred_kaspa_monitor, 'str')
        Setting.set_value('app.preferred_bitcoin_monitor', self._preferred_bitcoin_monitor, 'str')
        Setting.set_value('app.preferred_btc_wallet', self._preferred_btc_wallet, 'str')

    def get_service_status(self):
        """
        Get status summary of all services.
        """
        status = {
            'tor_service': {
                'name': self._tor_service.service_name if self._tor_service else 'Not initialized',
                'enabled': self._tor_service.is_enabled if self._tor_service else False,
                'detected': self._tor_service.is_detected if self._tor_service else False,
                'status': self._tor_service.service_status_string if self._tor_service else ''
            } if self._tor_service else None,

            'kaspad_service': {
                'name': self._kaspad_service.service_name if self._kaspad_service else 'Not initialized',
                'enabled': self._kaspad_service.is_enabled if self._kaspad_service else False,
                'detected': self._kaspad_service.is_detected if self._kaspad_service else False,
                'status': self._kaspad_service.service_status_string if self._kaspad_service else ''
            } if self._kaspad_service else None,

            'kaspa_wallet_service': {
                'name': self._kaspa_wallet_service.service_name if self._kaspa_wallet_service else 'Not initialized',
                'enabled': self._kaspa_wallet_service.is_enabled if self._kaspa_wallet_service else False,
                'detected': self._kaspa_wallet_service.is_detected if self._kaspa_wallet_service else False,
                'status': self._kaspa_wallet_service.service_status_string if self._kaspa_wallet_service else ''
            } if self._kaspa_wallet_service else None,

            'ln_wallet_service': {
                'name': self._ln_wallet_service.service_name if self._ln_wallet_service else 'Not initialized',
                'enabled': self._ln_wallet_service.is_enabled if self._ln_wallet_service else False,
                'detected': self._ln_wallet_service.is_detected if self._ln_wallet_service else False,
                'status': self._ln_wallet_service.service_status_string if self._ln_wallet_service else ''
            } if self._ln_wallet_service else None,

            'bitcoin_service': {
                'name': self._bitcoin_service.service_name if self._bitcoin_service else 'Not initialized',
                'enabled': self._bitcoin_service.is_enabled if self._bitcoin_service else False,
                'detected': self._bitcoin_service.is_detected if self._bitcoin_service else False,
                'status': self._bitcoin_service.service_status_string if self._bitcoin_service else ''
            } if self._bitcoin_service else None,

            'btc_wallet_service': {
                'name': self._btc_wallet_service.service_name if self._btc_wallet_service else 'Not initialized',
                'enabled': self._btc_wallet_service.is_enabled if self._btc_wallet_service else False,
                'detected': self._btc_wallet_service.is_detected if self._btc_wallet_service else False,
                'status': self._btc_wallet_service.service_status_string if self._btc_wallet_service else ''
            } if self._btc_wallet_service else None,
        }
        return status


# Test/example usage
async def test_service_manager():
    """Test the ServiceManager functionality"""
    print("Testing ServiceManager...")

    # Create service manager
    manager = ServiceManager()

    # Test service initialization
    print("Initializing services...")
    manager.initialize_services()

    # Check service properties
    print(f"Tor service: {manager.tor_service.service_name}")
    print(f"Kaspad service: {manager.kaspad_service.service_name}")
    print(f"Kaspa wallet service: {manager.kaspa_wallet_service.service_name}")
    print(f"LN wallet service: {manager.ln_wallet_service.service_name}")
    print(f"LN wallet service type: {type(manager.ln_wallet_service).__name__}")
    print(f"Bitcoin service: {manager.bitcoin_service.service_name}")
    print(f"Bitcoin service type: {type(manager.bitcoin_service).__name__}")
    print(f"BTC wallet service: {manager.btc_wallet_service.service_name}")
    print(f"BTC wallet service type: {type(manager.btc_wallet_service).__name__}")

    # Test wallet preference switching
    print(f"\nCurrent wallet preference: {manager._preferred_wallet}")
    current_wallet_type = type(manager.kaspa_wallet_service).__name__
    print(f"Current wallet service type: {current_wallet_type}")

    # Switch preference to external and see if it changes
    manager.set_preferred_kaspa_wallet('external')
    print(f"Switched to 'external' preference")
    new_wallet_type = type(manager.kaspa_wallet_service).__name__
    print(f"New wallet service type: {new_wallet_type}")

    # Switch back to go preference
    manager.set_preferred_kaspa_wallet('go')
    print(f"Switched to 'go' preference")
    final_wallet_type = type(manager.kaspa_wallet_service).__name__
    print(f"Final wallet service type: {final_wallet_type}")

    # Test kaspa monitor preference switching
    print(f"\nCurrent kaspa monitor preference: {manager._preferred_kaspa_monitor}")
    manager.set_preferred_kaspa_monitor('explorer')
    print(f"Kaspa service after explorer preference: {type(manager.kaspad_service).__name__}")
    manager.set_preferred_kaspa_monitor('kaspad')
    print(f"Kaspa service after kaspad preference: {type(manager.kaspad_service).__name__}")

    # Test bitcoin monitor preference switching
    print(f"\nCurrent bitcoin monitor preference: {manager._preferred_bitcoin_monitor}")
    manager.set_preferred_bitcoin_monitor('mempool')
    print(f"Bitcoin service after mempool preference: {type(manager.bitcoin_service).__name__}")
    manager.set_preferred_bitcoin_monitor('bitcoind')
    print(f"Bitcoin service after bitcoind preference: {type(manager.bitcoin_service).__name__}")

    # Test BTC wallet preference switching
    print(f"\nCurrent BTC wallet preference: {manager._preferred_btc_wallet}")
    manager.set_preferred_btc_wallet('bitcoind')
    print(f"BTC wallet after bitcoind preference: {type(manager.btc_wallet_service).__name__}")
    manager.set_preferred_btc_wallet('external')
    print(f"BTC wallet after external preference: {type(manager.btc_wallet_service).__name__}")

    # Test service detection
    print("\nRunning service detection...")
    await manager.detect_all_services()

    # Get status
    status = manager.get_service_status()
    print("\nService Status:")
    for service_name, service_info in status.items():
        if service_info:
            print(f"  {service_name}: {service_info['name']} - Detected: {service_info['detected']}, Enabled: {service_info['enabled']}")

    print("\nServiceManager test completed successfully!")


if __name__ == '__main__':
    asyncio.run(test_service_manager())
