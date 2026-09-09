"""Soft outlined-border pulse for “user action needed” cards."""

from kivy.animation import Animation


# Calm green pulse (not neon): bright ↔ dim on the card outline.
_ATTENTION_BRIGHT = (0.25, 0.82, 0.40, 1.0)
_ATTENTION_DIM = (0.25, 0.82, 0.40, 0.28)
_PULSE_S = 0.9


class AttentionPulseMixin:
    """Animate MDCard.line_color while style is outlined + theme_line_color Custom."""

    _attention_anim = None

    def start_attention_pulse(self):
        """Begin looping border pulse. Safe to call repeatedly."""
        self.stop_attention_pulse()
        self.style = "outlined"
        self.theme_line_color = "Custom"
        self.line_color = _ATTENTION_DIM
        anim = (
            Animation(line_color=_ATTENTION_BRIGHT, duration=_PULSE_S, t="in_out_sine")
            + Animation(line_color=_ATTENTION_DIM, duration=_PULSE_S, t="in_out_sine")
        )
        anim.repeat = True
        self._attention_anim = anim
        anim.start(self)

    def stop_attention_pulse(self):
        """Cancel pulse; leaves current line_color (caller may restyle)."""
        anim = getattr(self, "_attention_anim", None)
        if anim is not None:
            anim.cancel(self)
            self._attention_anim = None
