"""
Peer Selector Component - Compact peer selection with status
"""

import os

from kivy.animation import Animation
from kivy.properties import StringProperty, BooleanProperty, ListProperty
from kivy.metrics import dp
from kivy.clock import Clock
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.dialog import MDDialog, MDDialogHeadlineText, MDDialogContentContainer, MDDialogButtonContainer
from kivymd.uix.label import MDLabel, MDIcon
from kivymd.uix.divider import MDDivider
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.widget import MDWidget

# Display aliases only. selected_peer / actual_peer stay onions.
_PEER_NICKNAMES = {
    "exlg6u3252bnzit7mgia3tpb2yctafmo3wbev72pwmxeqg7jiywsx2yd.onion": "maker1",
    "mgipaoe5skumwnaiep6upojid6pk5xqupya7jcen22uhgaje2yty7mqd.onion": "maker2",
    "zca474adco66rxxg4ctt5apwwqcoljc6tpww5vjb3z3uvygireblesyd.onion": "maker3",
}


def peer_label(endpoint):
    if not endpoint:
        return ""
    if endpoint == "auto":
        return "Auto (Best Rate)"
    return _PEER_NICKNAMES.get(endpoint, endpoint)


class PeerSelectorCard(MDCard):
    """Compact peer selector with status indicator."""
    
    selected_peer = StringProperty("auto")  # Default to auto-select
    available_peers = ListProperty([
        "auto",  # Auto-select best rate
        "exlg6u3252bnzit7mgia3tpb2yctafmo3wbev72pwmxeqg7jiywsx2yd.onion",
        "mgipaoe5skumwnaiep6upojid6pk5xqupya7jcen22uhgaje2yty7mqd.onion",
        "zca474adco66rxxg4ctt5apwwqcoljc6tpww5vjb3z3uvygireblesyd.onion",
    ])
    actual_peer = StringProperty("")  # The actual peer being used (when auto)
    peer_status = StringProperty("online")  # online, offline, checking
    peer_rate_info = StringProperty("Rate: ...")
    is_collapsed = BooleanProperty(False)
    is_checking = BooleanProperty(False)  # True when fetching rates
    
    # Display properties
    display_peer_name = StringProperty("")  # Nickname (or onion if unknown)
    
    # Reference to parent screen
    screen = None
    
    def __init__(self, **kwargs):
        # --peer / MAKER_ENDPOINT pins the selector to a single onion.
        if peer := os.getenv("MAKER_ENDPOINT"):
            kwargs.setdefault("available_peers", [peer])
            kwargs.setdefault("selected_peer", peer)
        super().__init__(**kwargs)
        self.peer_menu = None
        self.info_dialog = None
        self.info_label = None
        
        # Bind to update display name when peer changes
        self.bind(selected_peer=self.update_display_name)
        
        self.setup_menu()
    
    def setup_menu(self):
        """Setup peer selection dropdown."""
        if not hasattr(self, 'ids') or 'peer_button' not in self.ids:
            # Schedule for when ids are ready
            Clock.schedule_once(lambda dt: self.setup_menu(), 0.1)
            return
        
        menu_items = [
            {
                "text": peer_label(peer),
                "on_release": lambda x=peer: self.select_peer(x),
            } for peer in self.available_peers
        ]
        longest = max(len(peer_label(p)) for p in self.available_peers)
        best_width = min(longest * dp(10), self.width * 0.6)
        self.peer_menu = MDDropdownMenu(
            caller=self.ids.peer_button,
            items=menu_items,
            width=best_width,
            hor_growth='right'
        )
    
    def show_peer_menu(self):
        """Show peer selection menu."""
        if self.peer_menu:
            self.peer_menu.open()
    
    def update_status(self, status, rate_info=None, editor=None):
        """Update peer status and rate info.
        
        Args:
            status: "online", "offline", or "checking"
            rate_info: Optional rate info string. If None and editor provided, auto-calculates
            editor: Optional SwapEditor reference to auto-calculate rate from
        """
        self.peer_status = status
        self.is_checking = (status == "checking")
        
        if rate_info is not None:
            self.peer_rate_info = rate_info
        elif editor is not None:
            self.peer_rate_info = self._format_rate_info(editor)
        # If neither rate_info nor editor provided, keep existing rate_info

    @staticmethod
    def _format_rate_info(editor) -> str:
        """Build rate display from the active mode's rate pair (LN vs on-chain)."""
        if editor.swap_mode == "onchain":
            mode_label = "On-chain"
            forward = editor.kas2btc_rate
            reverse = editor.btc2kas_rate
            forward_name = "KAS -> BTC"
            reverse_name = "BTC -> KAS"
        else:
            mode_label = "LN"
            forward = editor.kas2sat_rate
            reverse = editor.sat2kas_rate
            forward_name = "KAS -> SAT"
            reverse_name = "SAT -> KAS"

        current = editor.get_current_rate()
        if current <= 0 and forward <= 0 and reverse <= 0:
            return f"{mode_label} rate: ..."

        # Prefer the active direction; fall back to whichever rate we have.
        if current > 0:
            direction = editor.swap_direction
            dir_label = {
                "kas2sat": "KAS -> SAT",
                "sat2kas": "SAT -> KAS",
                "kas2btc": "KAS -> BTC",
                "btc2kas": "BTC -> KAS",
            }.get(direction, "")
            return f"{mode_label} {dir_label}: {current:.2f} sats/KAS"

        parts = []
        if forward > 0:
            parts.append(f"{forward_name} {forward:.2f}")
        if reverse > 0:
            parts.append(f"{reverse_name} {reverse:.2f}")
        return f"{mode_label}: " + " | ".join(parts) + " sats/KAS"
    
    def update_display_name(self, *args):
        """Nickname as-is; unmatched onions are truncated to the leftover button width."""
        peer = self.selected_peer
        label = peer_label(peer)
        if label != peer:
            self.display_peer_name = label
            return

        # Calculate and update display name with truncation based on available space.
        #
        # Steps:
        # 1. Get parent container width
        # 2. Subtract widths of sibling widgets
        # 3. Calculate available space for button
        # 4. Account for button padding/borders
        # 5. Truncate text to fit, which auto-resizes button

        # Get widgets
        if not hasattr(self, 'ids') or 'peer_button' not in self.ids:
            self.display_peer_name = peer
            return

        button = self.ids.peer_button
        if not button.parent or button.width == 0 or not button.children:
            self.display_peer_name = peer
            return

        parent_box = button.parent  # MDBoxLayout
        if parent_box.width == 0:
            self.display_peer_name = peer
            return

        # Step 1: Get parent width
        parent_width = parent_box.width

        # Step 2: Calculate widths of sibling widgets
        # Layout: [Peer Label] [Button] [Spacer] [Status Icon] [Info Button]
        siblings_width = 0
        spacer = self.ids.spacer
        for child in parent_box.children:
            # we ignore the spacer and the button itself
            if child != button and child != spacer:
                siblings_width += child.width

        # Step 3: Account for spacing between widgets (5 widgets = 4 gaps)
        spacing = dp(12) * (len(parent_box.children) - 1)

        # Step 4: Calculate available width for button (with safety margin)
        # Include button internal padding (~40dp for outlined button)
        button_padding = dp(40)
        safety_margin = dp(40)  # Extra safety

        available_for_text = parent_width - siblings_width - spacing - button_padding - safety_margin

        # Ensure minimum width
        if available_for_text < dp(100):
            available_for_text = dp(100)

        # Step 5: Measure text and truncate if needed
        button_text = button.children[0]  # MDButtonText

        # Measure full peer name, I believe most of this code is useless, unless we are enlarging the window, may need some better logic to handle this.
        original_text = button_text.text
        button_text.text = peer
        button_text.texture_update()
        full_width = button_text.texture_size[0]
        button_text.text = original_text  # Restore

        if full_width <= available_for_text:
            # Fits completely
            self.display_peer_name = peer
            return

        # Calculate truncation using texture-based ratio
        chars_fit = int(len(peer) * (available_for_text / full_width))
        side_chars = max(6, (chars_fit - 3) // 2)  # Min 6 chars per side

        if side_chars * 2 + 9 < len(peer):
            # +6 accounts for .onion suffix length
            self.display_peer_name = f"{peer[:side_chars]}...{peer[-(side_chars+6):]}"
        else:
            self.display_peer_name = peer
    
    def select_peer(self, peer):
        """Select a peer and restart rate refresh."""
        self.selected_peer = peer  # This triggers update_display_name via binding
        if self.peer_menu:
            self.peer_menu.dismiss()
        
        # Reset rates and restart refresh
        if self.screen:
            # Reset cached rates since peer changed
            self.screen.ids.swap_editor.last_quote = None
            self.screen.ids.swap_editor.kas2sat_rate = 0
            self.screen.ids.swap_editor.sat2kas_rate = 0
            self.screen.ids.swap_editor.kas2btc_rate = 0
            self.screen.ids.swap_editor.btc2kas_rate = 0
            
            if peer == "auto":
                self.update_status("checking", "Finding best rate...")
                self.actual_peer = ""
            else:
                self.update_status("checking", "Connecting to peer...")
                self.actual_peer = peer
            
            self.screen.restart_rate_refresh()
    
    def show_peer_info(self):
        """Show detailed peer information in dialog."""
        if self.info_dialog is not None:
            self.info_dialog.dismiss()

        self.info_label = MDLabel(
            text=self._info_dialog_text(),
            theme_text_color="Secondary",
            adaptive_height=True,
            font_style="Body",
            role="medium"
        )
        
        # Bind properties to update label in real-time
        self.bind(selected_peer=self._update_info_label)
        self.bind(actual_peer=self._update_info_label)
        self.bind(peer_status=self._update_info_label)
        self.bind(peer_rate_info=self._update_info_label)
        
        self.info_dialog = MDDialog(
            MDDialogHeadlineText(text="Peer Information"),
            MDDialogContentContainer(
                self.info_label,
            ),
            MDDialogButtonContainer(
                MDWidget(),
                MDButton(
                    MDButtonText(text="Close"),
                    style="text",
                    pos_hint={"right": 1},
                    on_release=lambda x: self._close_info_dialog(),
                )
            ),
            auto_dismiss=True,
        )
        # Unbind on any dismiss path (Close button or outside tap).
        self.info_dialog.bind(on_dismiss=self._on_info_dialog_dismissed)
        self.info_dialog.open()

    def _format_both_rates_for_mode(self) -> str:
        """Show both direction rates for the active chain mode."""
        editor = None
        if self.screen and hasattr(self.screen, 'ids') and 'swap_editor' in self.screen.ids:
            editor = self.screen.ids.swap_editor
        if editor is None:
            return self.peer_rate_info

        if editor.swap_mode == "onchain":
            return (
                f"On-chain rates:\n"
                f"  KAS -> BTC: {editor.kas2btc_rate:.2f} sats/KAS\n"
                f"  BTC -> KAS: {editor.btc2kas_rate:.2f} sats/KAS"
            )
        return (
            f"LN rates:\n"
            f"  KAS -> SAT: {editor.kas2sat_rate:.2f} sats/KAS\n"
            f"  SAT -> KAS: {editor.sat2kas_rate:.2f} sats/KAS"
        )
    
    def _info_dialog_text(self):
        endpoint = self.actual_peer if self.selected_peer == "auto" else self.selected_peer
        label = peer_label(endpoint) if endpoint else "Checking..."
        lines = [
            f"Mode: {'Auto-select' if self.selected_peer == 'auto' else 'Manual'}",
            f"Peer: {label}",
        ]
        if endpoint and label != endpoint:
            lines.append(endpoint)
        lines.append(f"Status: {self.peer_status}")
        lines.append(self._format_both_rates_for_mode())
        return "\n".join(lines)

    def _update_info_label(self, *args):
        if self.info_label:
            self.info_label.text = self._info_dialog_text()

    def _on_info_dialog_dismissed(self, *args):
        """Clear live binds whether closed by Close or outside tap."""
        self.unbind(selected_peer=self._update_info_label)
        self.unbind(actual_peer=self._update_info_label)
        self.unbind(peer_status=self._update_info_label)
        self.unbind(peer_rate_info=self._update_info_label)
        self.info_dialog = None
        self.info_label = None
    
    def _close_info_dialog(self):
        """Close dialog; unbind happens in on_dismiss."""
        if self.info_dialog:
            self.info_dialog.dismiss()
    
    def collapse(self):
        """Collapse peer selector with animation."""
        self.is_collapsed = True
        anim = Animation(height=0, opacity=0, duration=0.3)
        anim.start(self)
    
    def expand(self):
        """Expand peer selector with animation."""
        self.is_collapsed = False
        anim = Animation(height=dp(60), opacity=1, duration=0.3)
        anim.start(self)

