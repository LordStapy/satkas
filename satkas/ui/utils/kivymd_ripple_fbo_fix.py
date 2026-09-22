"""KivyMD 2.0.0 creates the M3 ripple FBO at self.size.

MDSnackbar KV sets height to minimum_height, which is 0 in __init__,
so Fbo((w, 0)) raises Incomplete attachment.

Upstream (not in 2.0.0):
https://github.com/kivymd/KivyMD/commit/0b8de393f4b7ca8d70f089236db0c76c8a5e645f

Drop this when KivyMD includes that commit.
"""


def apply_kivymd_ripple_fbo_fix():
    from kivy.graphics import ClearBuffers, ClearColor, Color, Fbo, Rectangle
    from kivymd.uix.behaviors.ripple_behavior import M3CommonRipple

    if getattr(M3CommonRipple.init_fbos, "_satkas_fbo_fix", False):
        return

    def init_fbos(self):
        self._phase = 0.0
        self.ripple_pos = (0, 0)
        self.fbo = Fbo(size=[50] * 2, group="m3_ripple_behavior")
        self.set_shader(self.fbo)
        with self.fbo:
            ClearColor(0, 0, 0, 0)
            ClearBuffers()
            Color(1, 1, 1, 1)
            self.rect = Rectangle(pos=(0, 0), size=[50] * 2)

    init_fbos._satkas_fbo_fix = True
    M3CommonRipple.init_fbos = init_fbos

    _update_uniforms = M3CommonRipple._update_uniforms

    def update_uniforms(self):
        if self.fbo.size != self.size:
            self.fbo.size = self.size
        _update_uniforms(self)

    M3CommonRipple._update_uniforms = update_uniforms
