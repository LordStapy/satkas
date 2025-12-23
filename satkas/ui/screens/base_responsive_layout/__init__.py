"""
Base Responsive Layout Package

A KivyMD-based responsive layout system that automatically adapts
to different screen sizes (desktop, tablet, mobile).
"""

from .base_responsive_layout import BaseResponsiveLayout
from .desktop import DesktopLayout, MainDesktopColumn, RightDesktopColumn
from .tablet import TabletLayout
from .mobile import MobileLayout

__all__ = [
    'BaseResponsiveLayout',
    'DesktopLayout', 'MainDesktopColumn', 'RightDesktopColumn',
    'TabletLayout',
    'MobileLayout'
]
__version__ = '0.1.0'
