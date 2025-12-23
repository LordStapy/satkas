
# ServiceManager class that manages all services

import asyncio
from satkas.core.db.models import Setting
from .tor_service import TorService
from .kaspad_service import KaspadService
from .kaspawallet_service import KaspawalletService
from .rustykaspawallet_service import RustyKaspaWalletService
from .external_kaspawallet_service import ExternalKaspaWalletService
from .lnbits_service import LNBitsService
from .external_ln_wallet_service import ExternalLNWalletService

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

        self.options = {
            'kaspa_wallet': ['go', 'rusty', 'external'],
            'ln_wallet': ['lnbits', 'external'],
        }
        # Preferred wallet implementation (can be configured)
        # Load from database with fallback to 'external'
        self._preferred_wallet = Setting.get_value('app.preferred_kaspa_wallet', 'external')
        self._preferred_ln_wallet = Setting.get_value('app.preferred_ln_wallet', 'external')

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
            self._kaspad_service = KaspadService()
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

    def _select_ln_wallet_service(self):
        """
        Select which LN wallet service to use.
        Priority: LNBitsService if available, otherwise ExternalLNWalletService.
        """

        services_to_try = []
        if self._preferred_ln_wallet == 'lnbits':
            services_to_try.append(LNBitsService)
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
        if self._preferred_wallet == 'rusty':
            services_to_try = [RustyKaspaWalletService, KaspawalletService]
        elif self._preferred_wallet == 'go':
            services_to_try = [KaspawalletService, RustyKaspaWalletService]
        elif self._preferred_wallet == 'external':
            services_to_try = [ExternalKaspaWalletService, RustyKaspaWalletService, KaspawalletService]

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


    def set_preferred_kaspa_wallet(self, preference):
        """
        Set preferred kaspa wallet implementation.
        :param preference: 'go', 'rusty', or 'external'
        """
        if preference not in ['go', 'rusty', 'external']:
            raise ValueError("Preference must be 'go', 'rusty', or 'external'")

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
        :param preference: 'lnbits', 'external'
        """
        if preference not in ['lnbits', 'external']:
            raise ValueError("Preference must be 'lnbits' or 'external'")

        if preference != self._preferred_ln_wallet:
            self._preferred_ln_wallet = preference
            # Reset the current LN wallet service so it will be re-selected on next access
            if self._ln_wallet_service is not None:
                # Clean up current service if needed
                if hasattr(self._ln_wallet_service, 'disable'):
                    self._ln_wallet_service.disable()
                self._ln_wallet_service = None
                self._notify_change(['ln_wallet_service'])

    def initialize_services(self):
        """
        Initialize all services. This ensures services are created and ready.
        """
        # Access each service property to trigger initialization
        _ = self.tor_service
        _ = self.kaspad_service
        _ = self.kaspa_wallet_service
        _ = self.ln_wallet_service

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
            self._kaspa_wallet_service.enable()
        if self._ln_wallet_service and self._ln_wallet_service.is_detected:
            self._ln_wallet_service.enable()

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
            } if self._ln_wallet_service else None
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

    # Test wallet preference switching
    print(f"\nCurrent wallet preference: {manager._preferred_wallet}")
    current_wallet_type = type(manager.kaspa_wallet_service).__name__
    print(f"Current wallet service type: {current_wallet_type}")

    # Switch preference to external and see if it changes
    manager.set_preferred_wallet('external')
    print(f"Switched to 'external' preference")
    new_wallet_type = type(manager.kaspa_wallet_service).__name__
    print(f"New wallet service type: {new_wallet_type}")

    # Switch back to go preference
    manager.set_preferred_wallet('go')
    print(f"Switched to 'go' preference")
    final_wallet_type = type(manager.kaspa_wallet_service).__name__
    print(f"Final wallet service type: {final_wallet_type}")

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
