"""UI media assets (icons and other static images)."""

import os

_DIR = os.path.dirname(os.path.abspath(__file__))

KASPA_ICON = os.path.join(_DIR, 'kaspa-icon-green.png')
BTC_ICON = os.path.join(_DIR, 'bitcoin_logo.png')

__all__ = ['KASPA_ICON', 'BTC_ICON']
