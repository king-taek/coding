"""Threshold 미리보기 결과를 HTML 한 장으로 — 기존 → 변경 값, 새로 빠지는 Defect 수, 분포 그림, 대표 사진.

현택님 결정(2026-11-15): ★ 저장 대신 **내보내기**.  파일 하나로 메일 · 보고에 그대로 붙이게
사진은 작은 JPEG 로 HTML 안에 넣는다(바깥 파일을 가리키지 않는다).

Qt 없이 돈다 — 사진은 부르는 쪽이 ``data:`` 주소로 바꿔 넘긴다.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from .. import i18n

__all__ = ["Change", "ZoneResult", "Sample", "pick_samples", "build_html"]

_KO = i18n.KO


@dataclass(frozen=True)
class Change:
    zone: str                   # "RDL · 레시피 2"
    rule: str                   # "기준 1 → B R Surface Dark"
    item: str                   # "Contrast"
    unit: str                   # "µm" · "µm²" · ""
    old: float                  # 레시피 값(µm)
    new: float
    dec: int
    values: tuple = ()          # 그 Zone · 분류 Defect 값(µm) — 분포 그림용


@dataclass(frozen=True)
class Sample:
    src: str                    # data: 주소 — 비면 사진 칸 없이 글자만
    caption: str
    value: str                  # 걸린 값, 예: "Contrast 25.6"


@dataclass(frozen=True)
class ZoneResult:
    zone: str
    total: int
    dropped: int
    samples: tuple = ()
    no_photo: int = 0
    appeared: int = 0           # 내린 값으로 새로 잡히는 후보(RichSurface) — 사진은 없다


def pick_samples(items: list, n: int) -> list:
    """정렬된 목록에서 ``n`` 개를 고르게 — 기준 바로 아래부터 멀리 떨어진 것까지 고루 보이게."""
    if n <= 0 or not items:
        return []
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[0]]
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


_CSS = """
:root{--ink:#1f2328;--muted:#6b7280;--line:#d8dbe0;--bg:#f6f5f2;--card:#fff;--drop:#b42318;--keep:#8a8270;--accent:#2f5d8a;--new:#1f7a4d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Malgun Gothic","Apple SD Gothic Neo",sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 20px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.mono{font-family:Consolas,"D2Coding",monospace}.muted{color:var(--muted)}
.stats{display:flex;gap:28px;align-items:flex-end;margin:18px 0 6px;flex-wrap:wrap}
.stat b{display:block;font-size:40px;line-height:1.1}.stat span{color:var(--muted);font-size:13px}
.stat.drop b{color:var(--drop)}.stat.new b,.plus{color:var(--new)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:12px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:12px;color:var(--muted);font-weight:600}td.num{font-family:Consolas,monospace;white-space:nowrap}
.arrow{color:var(--drop);font-weight:700}.plus{font-weight:700}
.bar{display:flex;height:14px;border-radius:3px;overflow:hidden;background:var(--keep);margin:6px 0 4px}
.bar i{display:block;background:var(--drop)}
.zone-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:10px}
.tile{border:1px solid var(--line);border-radius:6px;padding:6px;background:#fafafa}
.tile img{width:100%;aspect-ratio:1;object-fit:contain;background:#ddd;display:block;border-radius:3px}
.tile div{font-size:12px;margin-top:4px;display:flex;justify-content:space-between;gap:6px}
.tile .v{color:var(--drop);font-family:Consolas,monospace}
svg{display:block}
"""


def _hist_svg(values, old: float, new: float, w: int = 260, h: int = 54) -> str:
    """분포 막대 + 레시피 선(회색) + 바꾼 선(빨강).  새 기준 아래 막대는 빨강."""
    vals = [v for v in values if v == v]
    if not vals:
        return ""
    lo = min(min(vals), old, new)
    hi = max(max(vals), old, new)
    if hi <= lo:
        hi = lo + 1.0
    bins = 30
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int((v - lo) / width))] += 1
    top = max(counts) or 1
    bw = w / bins
    parts = []
    for i, c in enumerate(counts):
        if not c:
            continue
        bh = max(1.0, (h - 6) * c / top)
        left = lo + i * width
        color = "#b42318" if left + width <= new + 1e-12 else "#8a8270"
        parts.append(f'<rect x="{i * bw + 0.5:.1f}" y="{h - bh:.1f}" width="{bw - 1:.1f}" height="{bh:.1f}" fill="{color}"/>')

    def line(v, color, dash=""):
        x = (v - lo) / (hi - lo) * w
        return f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h}" stroke="{color}" stroke-width="2"{dash}/>'

    parts.append(line(old, "#6b7280", ' stroke-dasharray="3 3"'))
    parts.append(line(new, "#b42318" if new >= old else "#1f7a4d"))
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img">{"".join(parts)}</svg>'


def build_html(*, folder: str, when: str, total: int, dropped: int,
               changes: list, zones: list, appeared: int = 0) -> str:
    """보고서 HTML 문자열.  ``changes`` = :class:`Change` 들, ``zones`` = 바뀌는 게 있는 :class:`ZoneResult` 들."""
    e = html.escape
    out = ["<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>{e(_KO.THRESH_EXPORT_TITLE)}</title><style>{_CSS}</style></head><body><main>",
           f"<h1>{e(_KO.THRESH_EXPORT_TITLE)}</h1>",
           f"<div class='mono muted'>{e(folder)}</div><div class='muted'>{e(when)}</div>",
           "<div class='stats'>",
           f"<div class='stat drop'><b>{dropped:,}</b><span>{e(_KO.THRESH_PREVIEW_DROPPED)}</span></div>",
           (f"<div class='stat new'><b>+{appeared:,}</b><span>{e(_KO.THRESH_PREVIEW_APPEARED)}</span></div>"
            if appeared else ""),
           f"<div class='stat'><b>{total - dropped:,}</b><span>{e(_KO.THRESH_PREVIEW_TAB_KEEP)}</span></div>",
           f"<div class='stat'><b>{total:,}</b><span>{e(_KO.THRESH_EXPORT_ALL)}</span></div>",
           "</div>"]

    out.append(f"<h2>{e(_KO.THRESH_EXPORT_CHANGES)}</h2><div class='card'><table>")
    out.append("<tr>" + "".join(f"<th>{e(t)}</th>" for t in (
        _KO.THRESH_PREVIEW_ZONE, _KO.THRESH_PREVIEW_RULE, _KO.THRESH_EXPORT_ITEM,
        _KO.THRESH_EXPORT_OLD_NEW, _KO.THRESH_EXPORT_SHAPE)) + "</tr>")
    for c in changes:
        unit = f" {e(c.unit)}" if c.unit else ""
        out.append(
            f"<tr><td>{e(c.zone)}</td><td>{e(c.rule)}</td><td>{e(c.item)}</td>"
            f"<td class='num'>{c.old:.{c.dec}f} <span class='{'arrow' if c.new >= c.old else 'plus'}'>"
            f"→ {c.new:.{c.dec}f}</span>{unit}</td>"
            f"<td>{_hist_svg(c.values, c.old, c.new)}</td></tr>")
    out.append("</table></div>")

    for z in zones:
        pct = 100.0 * z.dropped / z.total if z.total else 0.0
        out.append("<h2>Zone " + e(z.zone) + "</h2><div class='card'>")
        out.append("<div class='zone-head'>"
                   f"<span><b class='arrow'>−{z.dropped:,}</b>"
                   + (f" <b class='plus'>+{z.appeared:,}</b>" if z.appeared else "") + f" / {z.total:,}</span>"
                   f"<span class='muted'>{e(_KO.THRESH_PREVIEW_TAB_KEEP)} {z.total - z.dropped:,}"
                   + (f" · {e(_KO.THRESH_PREVIEW_NO_PHOTO.format(n=f'{z.no_photo:,}'))}" if z.no_photo else "")
                   + "</span></div>")
        out.append(f"<div class='bar'><i style='width:{pct:.2f}%'></i></div>")
        if z.samples:
            out.append("<div class='grid'>")
            for s in z.samples:
                img = f"<img src='{e(s.src, quote=True)}' alt=''>" if s.src else ""
                out.append(f"<div class='tile'>{img}<div><span>{e(s.caption)}</span>"
                           f"<span class='v'>{e(s.value)}</span></div></div>")
            out.append("</div>")
        out.append("</div>")
    out.append("</main></body></html>")
    return "\n".join(out)
