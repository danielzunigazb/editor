"""{"op":"pip","src":"C","start":2,"dur":3,"pos":"top-right","scale":0.3,"opacity":0.9}: a picture-in-picture video layer."""
from ... import anim as animmod
from ...ops import Op, op
from ...ops.common import POS_PIP, anim, off_video

_WHERE = {"top-right": "arriba a la derecha", "top-left": "arriba a la izquierda", "bottom-right": "abajo a la derecha", "bottom-left": "abajo a la izquierda"}


@op
class Pip(Op):
    name = "pip"
    animatable = True

    def layout(self, o, n, where, st):
        clip_len = st.ctx.CLIP_LEN
        bad = off_video(o.get("anim"))
        if bad:
            ok = [p for p in animmod.PRESETS if animmod.preset(p).on_video and not animmod.preset(p).callout_only and not animmod.preset(p).still]
            raise ValueError(f"{where}: anim '{bad[0]}' is not available on pip (it crops a still layer); use {', '.join(ok)}")
        src = o.get("src")
        if src not in clip_len:
            raise ValueError(f"{where}: unknown source '{src}'; known: {sorted(clip_len)}")
        if o.get("pos", "top-right") not in POS_PIP:
            raise ValueError(f"{where}: pos must be {'|'.join(POS_PIP)}")
        if not 0 < o.get("scale", 0.3) <= 1 or not 0 <= o.get("opacity", 1.0) <= 1:
            raise ValueError(f"{where}: scale must be in (0,1] and opacity in [0,1]")
        if o["start"] < 0 or o["dur"] <= 0 or o.get("in", 0.0) < 0 or o.get("in", 0.0) + o["dur"] > clip_len[src] + 1e-6:
            raise ValueError(f"{where}: needs start>=0, dur>0 and in+dur <= source length {clip_len[src]:g}s")
        st.layers.append({"kind": "pip", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "src": src,
                          "in": float(o.get("in", 0.0)), "pos": o.get("pos", "top-right"),
                          "scale": float(o.get("scale", 0.3)), "opacity": float(o.get("opacity", 1.0)), "anim": anim(o, where, st.ctx)})

    def describe(self, o):
        return (f"Picture-in-picture: clip {o['src']} de {o['start']:g} a {o['start']+o['dur']:g} s, {_WHERE.get(o.get('pos', 'top-right'), o.get('pos'))}",
                f"pip {o['src']} @ {o['start']:g}s {o['dur']:g}s")
