
import os
from abc import ABC, abstractmethod
from satkas.core.db.models import Setting


class ExternalWalletRequired(Exception):
    """The configured wallet is external, so the user has to act manually.

    Raised by the External* services on any operation that would move funds or
    create an invoice. Callers should surface it rather than treat it as a
    failure: it means the operation is still possible, just not automatable.
    """


class PaymentStatus:
    """What check_payment() answers about an outgoing lightning payment.

    The distinction that matters is FAILED versus UNKNOWN. FAILED means the
    wallet answered and nothing left it, so a swap built on that payment may be
    written off. UNKNOWN means we could not ask, and IN_FLIGHT means the
    payment may still settle: in both cases the money may yet be gone, so
    nothing terminal may be recorded.
    """

    SETTLED = 'settled'
    IN_FLIGHT = 'in_flight'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


class BaseService(ABC):
    can_refresh = True
    service_icon = "help-circle-outline"
    icon_style = ""

    def __init__(self, *args, **kwargs):
        # List of callables to be notified when fields change
        # Each observer is called as: observer(changed_fields: list[str])
        self._observers = []

    # Observer management (Kivy-agnostic)
    def register_observer(self, callback):
        if callback and callback not in self._observers:
            self._observers.append(callback)

    def unregister_observer(self, callback):
        if callback in self._observers:
            self._observers.remove(callback)

    def _notify_change(self, changed_fields=None):
        fields = changed_fields if changed_fields else []
        # Copy to avoid modification during iteration
        for observer in list(self._observers):
            try:
                observer(fields)
            except Exception:
                # Best-effort notify; ignore observer exceptions
                pass

    @abstractmethod
    def detect(self):
        return False

    @abstractmethod
    def parse_config_string(self, *args):
        pass

    @property
    @abstractmethod
    def config_string(self):
        return ''

    def load_config(self):
        """
        Load configuration into service attributes.
        Priority: Environment Variables > Database > Default > Fallback

        Format 3: {setting_key: {'attr': ..., 'default': ..., 'fallback': ..., 'type': ..., 'env': ...}}
        """
        if not hasattr(self, 'configs') or not isinstance(self.configs, dict):
            return

        service_name_clean = self.service_name.lower().replace(' ', '_').replace('(', '').replace(')', '')
        service_key = f"service.{service_name_clean}"

        for setting_key, config_spec in self.configs.items():
            # Only support Format 3 now
            if not isinstance(config_spec, dict):
                continue

            attr_name = config_spec.get('attr')
            default_value = config_spec.get('default')
            fallback_value = config_spec.get('fallback')
            type_hint = config_spec.get('type')
            env_var = config_spec.get('env')
            

            if not attr_name:
                continue

            # Priority 1: Environment Variables (highest priority)
            value = None
            if env_var:
                env_value = os.getenv(env_var)
                if env_value is not None:
                    value = self._convert_env_value(env_value, type_hint)

            # Priority 2: Database
            if value is None:
                full_key = f"{service_key}.{setting_key}"
                value = Setting.get_value(full_key)

            # Priority 3: Default value (can be attribute name or direct value)
            if value is None and default_value is not None:
                value = default_value

            # Priority 4: Fallback value
            if value is None:
                value = fallback_value

            # Set attribute if we have a value
            if value is not None:
                setattr(self, attr_name, value)

    def save_config(self):
        """
        Save configuration from service attributes to database.
        Only saves non-None values that differ from defaults.
        """
        if not hasattr(self, 'configs') or not isinstance(self.configs, dict):
            return

        service_name_clean = self.service_name.lower().replace(' ', '_').replace('(', '').replace(')', '')
        service_key = f"service.{service_name_clean}"
        for setting_key, config_spec in self.configs.items():
            # Only support Format 3 now
            if not isinstance(config_spec, dict):
                continue
            attr_name = config_spec.get('attr')
            default_value = config_spec.get('default')
            fallback_value = config_spec.get('fallback')
            type_hint = config_spec.get('type')

            if not attr_name:
                continue

            # Get current attribute value
            value = getattr(self, attr_name, None)
            if value is not None:
                # Use specified type or infer it
                if type_hint:
                    value_type = self._get_value_type_name(type_hint)
                else:
                    value_type = self._infer_value_type(value)

                full_key = f"{service_key}.{setting_key}"
                # print(f"Saving config: {full_key} = {value} ({value_type})")
                Setting.set_value(full_key, value, value_type)

    def reload_config(self):
        """
        Reload configuration from current database/environment state.
        Services can override this to add custom reconnection logic.
        """
        self.load_config()
        self._notify_change(['config_reloaded'])

    def _convert_env_value(self, env_value: str, type_hint):
        """Convert environment variable string to appropriate type."""
        if not type_hint or type_hint == str:
            return env_value

        try:
            if type_hint == int:
                return int(env_value)
            elif type_hint == float:
                return float(env_value)
            elif type_hint == bool:
                return env_value.lower() in ('true', '1', 'yes', 'on')
            else:
                return env_value
        except (ValueError, AttributeError):
            return None

    def _get_value_type_name(self, type_hint):
        """Convert Python type to string type name."""
        if type_hint == str:
            return 'str'
        elif type_hint == int:
            return 'int'
        elif type_hint == float:
            return 'float'
        elif type_hint == bool:
            return 'bool'
        elif type_hint in (dict, list):
            return 'json'
        else:
            return 'str'

    def _infer_value_type(self, value):
        """Infer value type name from Python value."""
        if isinstance(value, bool):
            return 'bool'
        elif isinstance(value, int):
            return 'int'
        elif isinstance(value, float):
            return 'float'
        elif isinstance(value, (dict, list)):
            return 'json'
        else:
            return 'str'
