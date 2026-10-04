"""glass: dark glass panels with a metallic gradient side bar and keylines; gradient titles; a ring + staff + glass flag for labels;
a double keyline frame with diamonds. Colours come from the theme (paper = glass, accent = metal, ink = subtitle text) and the theme's
shape options: gradient (metal stops for titles and bars), sub_tracking (tracked capitals under titles)."""
from PIL import Image, ImageDraw, ImageFilter

from ... import themes
from ...render import text as T
from ...shapes import CALLOUT_SIDES, CALLOUT_SUB_MAX, CALLOUT_TITLE_MAX, Shape, fit, register

SS = 2                # supersampling factor for smooth thin lines


def metal(th):
    return th.opt("shape", "gradient") or ((0.0, th.accent), (1.0, th.accent))


def keyline_frame(W, H, amount, color):
    """Double keyline with small diamonds at the corners and edge midpoints."""
    w, h = W * SS, H * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inset = amount * w
    lw = max(2, int(round(0.0022 * min(w, h))))
    gap = 0.011 * min(w, h)
    col = tuple(color) + (235,)
    d.rectangle([inset, inset, w - inset, h - inset], outline=col, width=lw)
    d.rectangle([inset + gap, inset + gap, w - inset - gap, h - inset - gap], outline=tuple(color) + (170,), width=max(1, lw // 2))
    r = 0.0085 * min(w, h)
    for cx, cy in [(inset, inset), (w - inset, inset), (inset, h - inset), (w - inset, h - inset),
                   (w / 2, inset), (w / 2, h - inset), (inset, h / 2), (w - inset, h / 2)]:
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=col)
    return img.resize((W, H), Image.LANCZOS)


@register
class Glass(Shape):
    name = "glass"

    def frame(self, W, H, amount, th):
        return keyline_frame(W, H, amount, themes.rgb(th.accent))

    def tile(self, layer, rect, th, H, key):
        ImageDraw.Draw(layer).rounded_rectangle(rect, radius=0.012 * H, fill=th.color("paper", 215), outline=th.color("accent", 200), width=max(2, int(0.0025 * H)))
        return th.color("accent"), th.color(th.card.get("sub", "muted")), False

    def lower_third(self, W, H, title, subtitle, align, strict, th):
        """Glass panel with a metal side bar; title in the metal gradient, subtitle in tracked capitals."""
        ts, ss = th.title_style, th.caption_style
        gold, grad_stops, sub_tr = themes.rgb(th.accent), metal(th), th.opt("shape", "sub_tracking")
        title = T.clean(title, ts)
        subtitle = T.clean(subtitle, ss) if subtitle else ""
        if "\n" in title or "\n" in subtitle:
            raise ValueError("lower third title and subtitle must be single lines")
        if len(title) > 60 or len(subtitle) > 80:
            raise ValueError("lower third: title max 60 characters, subtitle max 80")
        max_w = 0.78 * W
        ft = fit(title, ts, 0.046 * H, max_w)
        fs = fit(subtitle, ss, 0.0185 * H, max_w, sub_tr) if subtitle else None
        if ft is None or (subtitle and fs is None):
            if strict:
                raise ValueError("lower third text is too long to fit; shorten the title or subtitle")
            ft = ft or (T.make_font(ts, 0.046 * H * T.MIN_SHRINK), 0)
            fs = fs or (T.make_font(ss, 0.0185 * H * T.MIN_SHRINK), 0)
        (tf, ttr), sub = ft, (fs if subtitle else None)
        sub_txt = subtitle.upper()
        tw = T.text_width(title, tf, ttr)
        sw = T.text_width(sub_txt, sub[0], sub[1]) if sub else 0
        ta, td = tf.getmetrics()
        thh = (ta + td)
        sh = sum(sub[0].getmetrics()) if sub else 0
        pad = 0.022 * H
        gapv = 0.012 * H if sub else 0
        ph = thh + gapv + sh + 2 * pad
        pw = min(0.92 * W, max(tw, sw) + 2 * pad + 0.016 * W)
        margin = 0.06 * W
        x0 = margin if align == "left" else W - margin - pw
        y1 = H - 0.09 * H
        y0 = y1 - ph
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([x0, y0, x0 + pw, y1], radius=0.012 * H, fill=th.color("paper", 165), outline=gold + (110,), width=max(1, int(H * 0.0012)))
        bar = max(3, int(0.006 * W))
        bx = x0 if align == "left" else x0 + pw - bar
        img.paste(T.gradient(bar, int(ph), grad_stops), (int(bx), int(y0)))
        tx = x0 + pad + (bar if align == "left" else 0)
        ty = y0 + pad
        mask = Image.new("L", (W, H), 0)
        T.draw_tracked(ImageDraw.Draw(mask), tx, ty, title, tf, ttr, 255)
        sd = Image.new("L", (W, H), 0); sd.paste(mask, (0, int(round(0.05 * tf.size))))
        sd = sd.filter(ImageFilter.GaussianBlur(max(0.5, 0.07 * tf.size))).point(lambda v: int(v * 0.7))
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 255)); shadow.putalpha(sd)
        img = Image.alpha_composite(img, shadow)
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0)); grad.paste(T.gradient(W, int(thh), grad_stops), (0, int(ty)))
        grad.putalpha(mask)
        img = Image.alpha_composite(img, grad)
        if sub:
            sm = Image.new("L", (W, H), 0)
            T.draw_tracked(ImageDraw.Draw(sm), tx, ty + thh + gapv, sub_txt, sub[0], sub[1], 255)
            ivory = Image.new("RGBA", (W, H), th.color("ink")); ivory.putalpha(sm)
            img = Image.alpha_composite(img, ivory)
        return img

    def callout(self, W, H, title, subtitle, side, strict, th):
        """A metal ring on the exact point + a thin staff + a glass flag with the name. Returns (RGBA image, ax, ay): the image is SMALL and
        (ax, ay) is where the ring centre is inside it. side: ne/nw/se/sw = flag up-right, up-left, down-right, down-left."""
        ts, ss = th.title_style, th.caption_style
        gold, grad_stops, sub_tr = themes.rgb(th.accent), metal(th), th.opt("shape", "sub_tracking")
        if side not in CALLOUT_SIDES[1:]:
            raise ValueError(f"callout side must be one of {CALLOUT_SIDES[1:]} (or 'auto' in the editor)")
        title = T.clean(title, ts)
        subtitle = T.clean(subtitle, ss) if subtitle else ""
        if "\n" in title or "\n" in subtitle:
            raise ValueError("callout title and subtitle must be single lines")
        if not title or len(title) > CALLOUT_TITLE_MAX or len(subtitle) > CALLOUT_SUB_MAX:
            raise ValueError(f"callout: title 1-{CALLOUT_TITLE_MAX} characters, subtitle max {CALLOUT_SUB_MAX}")
        S = SS * 2
        h_ = H * S
        max_w = 0.30 * W * S
        ft = fit(title, ts, 0.030 * h_, max_w)
        fs = fit(subtitle, ss, 0.0135 * h_, max_w, sub_tr) if subtitle else None
        if ft is None or (subtitle and fs is None):
            if strict:
                raise ValueError("callout text is too long to fit; shorten the title or subtitle")
            ft = ft or (T.make_font(ts, 0.030 * h_ * T.MIN_SHRINK), 0)
            fs = fs or (T.make_font(ss, 0.0135 * h_ * T.MIN_SHRINK), 0)
        (tf, ttr), sub = ft, (fs if subtitle else None)
        sub_txt = subtitle.upper()
        tw = T.text_width(title, tf, ttr)
        sw = T.text_width(sub_txt, sub[0], sub[1]) if sub else 0
        thh, sh = sum(tf.getmetrics()), (sum(sub[0].getmetrics()) if sub else 0)
        pad = 0.011 * h_
        gapv = 0.006 * h_ if sub else 0
        ph, pw = thh + gapv + sh + 2 * pad, max(tw, sw) + 2 * pad
        R, dot = 0.0085 * h_, 0.0028 * h_                 # ring radius, centre dot
        stem = 0.035 * h_                                  # staff beyond the flag
        m = 0.004 * h_
        lw = max(2, int(round(0.0016 * h_)))
        right, up = side[1] == "e", side[0] == "n"
        iw = int(R + m + pw + m)
        ih = int(ph + stem + 2 * R + 2 * m)
        ax = (R + m) if right else (iw - R - m)
        ay = (ih - R - m) if up else (R + m)
        img = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        g = gold + (255,)
        fx0 = ax if right else ax - pw                     # flag rectangle, attached to the staff
        fy0 = 0 if up else ih - ph
        d.rectangle([ax - lw / 2, (0 if up else ay + R), ax + lw / 2, (ay - R if up else ih)], fill=g)      # staff
        d.rounded_rectangle([fx0, fy0, fx0 + pw, fy0 + ph], radius=0.006 * h_, fill=th.color("paper", 175), outline=gold + (120,), width=max(1, lw // 2))
        d.ellipse([ax - R - lw, ay - R - lw, ax + R + lw, ay + R + lw], outline=(0, 0, 0, 120), width=lw)   # dark halo: legible on bright ground
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=g, width=lw)
        d.ellipse([ax - dot, ay - dot, ax + dot, ay + dot], fill=g)
        tx = fx0 + pad
        mask = Image.new("L", (iw, ih), 0)
        T.draw_tracked(ImageDraw.Draw(mask), tx, fy0 + pad, title, tf, ttr, 255)
        grad = Image.new("RGBA", (iw, ih), (0, 0, 0, 0)); grad.paste(T.gradient(iw, int(thh), grad_stops), (0, int(fy0 + pad)))
        grad.putalpha(mask)
        img = Image.alpha_composite(img, grad)
        if sub:
            sm = Image.new("L", (iw, ih), 0)
            T.draw_tracked(ImageDraw.Draw(sm), tx, fy0 + pad + thh + gapv, sub_txt, sub[0], sub[1], 255)
            ivory = Image.new("RGBA", (iw, ih), th.color("ink")); ivory.putalpha(sm)
            img = Image.alpha_composite(img, ivory)
        ow, oh = max(2, int(round(iw / S))), max(2, int(round(ih / S)))
        img = img.resize((ow, oh), Image.LANCZOS)
        return img, ax / S, ay / S
