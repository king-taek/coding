"""Threshold 미리보기 결과를 HTML 한 장으로 — 기존 → 변경 값, 새로 빠지는 Defect 수, 분포 그림, 대표 사진.

현택님 결정(2026-11-15): ★ 저장 대신 **내보내기**.  파일 하나로 메일 · 보고에 그대로 붙이게
사진은 작은 JPEG 로 HTML 안에 넣는다(바깥 파일을 가리키지 않는다).

Qt 없이 돈다 — 사진은 부르는 쪽이 ``data:`` 주소로 바꿔 넘긴다.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass

from .. import i18n

__all__ = ["Change", "ZoneResult", "Sample", "pick_samples", "build_html", "live_strings"]

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
               changes: list, zones: list, appeared: int = 0, live: dict | None = None) -> str:
    """보고서 HTML 문자열.  ``changes`` = :class:`Change` 들, ``zones`` = 바뀌는 게 있는 :class:`ZoneResult` 들.

    ``live`` 를 주면 아래에 눌러 보는 부분(Defect Map · 사진 · 정보 · 고른 목록 · 웨이퍼 표)을 더한다(현택님 2026-11-24 안 8).
    모양은 :func:`_live_block` 이 정한다."""
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
    if live is not None:
        out.append(_live_block(live, changes))
    out.append("</main></body></html>")
    return "\n".join(out)


def live_strings() -> dict:
    """눌러 보는 부분이 쓰는 화면 문구 — ko.py 에서 모아 JSON 으로 넘긴다."""
    k = _KO
    return {"before": k.REPORT_BEFORE, "after": k.REPORT_AFTER, "map": k.REPORT_MAP, "photos": k.REPORT_PHOTOS,
            "picked": k.REPORT_PICKED, "wafers": k.REPORT_WAFERS, "copy": k.REPORT_COPY, "copied": k.REPORT_COPIED,
            "csv": k.REPORT_CSV, "all": k.THRESH_EXPORT_ALL, "drop": k.THRESH_PREVIEW_DROPPED,
            "keep": k.THRESH_PREVIEW_TAB_KEEP, "defect": k.OVERKILL_MARK_DEFECT, "overkill": k.OVERKILL_MARK_OVERKILL,
            "tabMarked": k.REPORT_TAB_MARKED, "sortBase": k.OVERKILL_SORT_BASE, "zone": "Zone",
            "cls": k.REPORT_CLASS, "wafer": k.REPORT_WAFER, "lot": "LOT", "slot": k.IMAGE_INFO_ROW_SLOT,
            "file": k.IMAGE_INFO_ROW_FILE, "result": k.REPORT_RESULT, "mark": k.REPORT_MARK,
            "noPhoto": k.REPORT_NO_PHOTO, "close": k.OVERKILL_DETAIL_CLOSE, "none": k.REPORT_NONE,
            "changes": k.THRESH_EXPORT_CHANGES, "item": k.THRESH_EXPORT_ITEM, "rule": k.THRESH_PREVIEW_RULE,
            "recipe": k.REPORT_RECIPE, "changed": k.REPORT_CHANGED}


_LIVE_CSS = """
.seg{display:inline-flex;flex-wrap:wrap;max-width:100%;border:1px solid var(--line);border-radius:6px;overflow:hidden;margin:0 8px 8px 0}
.seg button{border:0;background:var(--card);padding:6px 12px;font:inherit;cursor:pointer;color:var(--muted)}
.seg button.on{background:var(--accent);color:#fff}
.bar2{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
select,.btn{font:inherit;padding:5px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);cursor:pointer}
.btn:hover{border-color:var(--accent)}
#map svg{background:#fff;border:1px solid var(--line);border-radius:8px;max-width:100%;height:auto}
#map circle{cursor:pointer}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}
.cardp{border:1px solid var(--line);border-radius:6px;background:#fafafa;padding:5px;cursor:pointer;position:relative}
.cardp:hover{border-color:var(--accent)}.cardp img{width:100%;aspect-ratio:1;object-fit:contain;background:#ddd;display:block}
.cardp .c{font-size:12px;display:flex;justify-content:space-between;gap:4px;margin-top:3px}
.tag{font-size:11px;padding:0 6px;border-radius:9px;color:#fff;position:absolute;top:8px;left:8px}
.tag.d{background:var(--accent)}.tag.o{background:#b26a00}
.res-drop{color:var(--drop);font-weight:700}
#info{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:9}
#info .box{background:var(--card);border-radius:10px;max-width:880px;width:94vw;max-height:92vh;overflow:auto;padding:16px;display:flex;gap:16px;flex-wrap:wrap}
#info img{width:400px;max-width:100%;object-fit:contain;background:#ddd}
#info table{width:auto;min-width:300px}
.hl{stroke:#000;stroke-width:2}
.card{overflow-x:auto}
"""

_LIVE_JS = r"""
(function(){
const D=JSON.parse(document.getElementById('live-data').textContent),T=D.t;
const $=s=>document.querySelector(s),el=(t,a,...k)=>{const e=document.createElement(t);Object.assign(e,a||{});k.forEach(c=>e.append(c));return e};
let after=true,wafer=0,tab='marked',fz='',fc='',sortKey='';
const F=['area','width','length','contrast'],FN={area:'Area',width:'Width',length:'Length',contrast:'Contrast'};
const dropped=d=>after&&d.r===1,res=d=>dropped(d)?T.drop:T.keep;
const markName=d=>d.m===1?T.defect:d.m===2?T.overkill:'';
function seg(host,items,cur,on){host.innerHTML='';const s=el('div',{className:'seg'});items.forEach(([k,n])=>{const b=el('button',{textContent:n,className:k===cur?'on':''});b.onclick=()=>on(k);s.append(b)});host.append(s)}
function copyText(txt,btn){const done=()=>{const o=btn.textContent;btn.textContent=T.copied;setTimeout(()=>btn.textContent=o,1200)};
 if(navigator.clipboard)navigator.clipboard.writeText(txt).then(done,()=>fallback());else fallback();
 function fallback(){const t=el('textarea',{value:txt});document.body.append(t);t.select();document.execCommand('copy');t.remove();done()}}
function info(i){const d=D.defects[i],w=D.wafers[d.w],box=$('#info .box');box.innerHTML='';
 if(d.p>=0)box.append(el('img',{src:D.photos[d.p]}));else box.append(el('div',{className:'muted',textContent:T.noPhoto}));
 const t=el('table');const row=(a,b)=>t.append(el('tr',{},el('th',{textContent:a}),el('td',{className:'num',textContent:b})));
 row(T.file,d.f||'—');row(T.wafer,w.name);row(T.lot,w.lot||'—');row(T.slot,w.slot||'—');row(T.zone,D.zones[d.z]);row(T.cls,D.classes[d.c]);
 row('X, Y (µm)',d.x.toFixed(1)+', '+d.y.toFixed(1));F.forEach(k=>{if(d[k]!==null)row(FN[k],d[k])});row(T.result,res(d));row(T.mark,markName(d)||'—');
 const side=el('div',{},t,el('button',{className:'btn',textContent:T.close,onclick:()=>{$('#info').style.display='none'}}));box.append(side);
 $('#info').style.display='flex';if(D.map&&d.w!==wafer){wafer=d.w;drawMap()}hl(i)}
function hl(i){document.querySelectorAll('#map circle.hl').forEach(c=>c.classList.remove('hl'));const c=document.getElementById('dot'+i);if(c)c.classList.add('hl')}
function drawMap(){if(!D.map)return;const host=$('#map');host.innerHTML='';
 seg(host,D.wafers.map((w,i)=>[i,w.name]),wafer,k=>{wafer=k;drawMap()});
 const ds=D.defects.map((d,i)=>[d,i]).filter(([d])=>d.w===wafer);if(!ds.length){host.append(el('div',{className:'muted',textContent:T.none}));return}
 let x0=Infinity,x1=-Infinity,y0=Infinity,y1=-Infinity;ds.forEach(([d])=>{x0=Math.min(x0,d.x);x1=Math.max(x1,d.x);y0=Math.min(y0,d.y);y1=Math.max(y1,d.y)});
 const S=520,pad=12,span=Math.max(x1-x0,y1-y0)||1,sx=v=>pad+(v-x0)/span*(S-2*pad),sy=v=>S-pad-(v-y0)/span*(S-2*pad);
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 '+S+' '+S);svg.setAttribute('width',S);svg.setAttribute('height',S);
 ds.sort((a,b)=>(a[0].m>0)-(b[0].m>0)||(dropped(a[0]))-(dropped(b[0])));
 ds.forEach(([d,i])=>{const c=document.createElementNS(ns,'circle');c.id='dot'+i;c.setAttribute('cx',sx(d.x).toFixed(1));c.setAttribute('cy',sy(d.y).toFixed(1));
  c.setAttribute('r',d.m?5:3);c.setAttribute('fill',dropped(d)?'#b42318':'#8a8270');
  if(d.m){c.setAttribute('stroke',d.m===1?'#2f5d8a':'#b26a00');c.setAttribute('stroke-width','2')}
  c.append(document.createElementNS(ns,'title'));c.firstChild.textContent=D.classes[d.c]+' · '+res(d);c.onclick=()=>info(i);svg.append(c)});
 host.append(svg)}
function drawCards(){const host=$('#cards');host.innerHTML='';
 let ds=D.defects.map((d,i)=>[d,i]).filter(([d])=>d.p>=0);
 ds=ds.filter(([d])=>tab==='marked'?d.m>0:tab==='drop'?dropped(d):tab==='keep'?!dropped(d):true);
 if(fz!=='')ds=ds.filter(([d])=>d.z===+fz);if(fc!=='')ds=ds.filter(([d])=>d.c===+fc);
 if(sortKey)ds.sort((a,b)=>(b[0][sortKey]??-1)-(a[0][sortKey]??-1));
 if(!ds.length){host.append(el('div',{className:'muted',textContent:T.none}));return}
 ds.forEach(([d,i])=>{const c=el('div',{className:'cardp',onclick:()=>info(i)},el('img',{src:D.photos[d.p],loading:'lazy'}),
  el('div',{className:'c'},el('span',{textContent:D.classes[d.c]}),el('span',{className:dropped(d)?'res-drop':'',textContent:sortKey&&d[sortKey]!==null?d[sortKey]:res(d)})));
  if(d.m)c.append(el('span',{className:'tag '+(d.m===1?'d':'o'),textContent:markName(d)}));host.append(c)})}
function drawPhotos(){const host=$('#photo-bar');host.innerHTML='';const a=el('div'),b=el('div',{className:'bar2'});host.append(a,b);
 seg(a,[['marked',T.tabMarked],['drop',T.drop],['keep',T.keep],['all',T.all]],tab,k=>{tab=k;drawPhotos()});
 const sel=(opts,cur,on)=>{const s=el('select');opts.forEach(([v,n])=>s.append(el('option',{value:v,textContent:n,selected:String(v)===String(cur)})));s.onchange=()=>on(s.value);return s};
 b.append(sel([['',T.zone+' · '+T.all],...D.zones.map((n,i)=>[i,n])],fz,v=>{fz=v;drawCards()}),
  sel([['',T.cls+' · '+T.all],...D.classes.map((n,i)=>[i,n])],fc,v=>{fc=v;drawCards()}),
  sel([['',T.sortBase],...F.map(k=>[k,FN[k]])],sortKey,v=>{sortKey=v;drawCards()}));drawCards()}
function pickedRows(){return D.defects.filter(d=>d.m>0).map(d=>{const w=D.wafers[d.w];return [markName(d),d.f,w.name,w.lot,w.slot,D.zones[d.z],d.i,d.x.toFixed(3),d.y.toFixed(3),d.area??'',d.width??'',d.length??'',d.contrast??'']})}
function drawPicked(){const host=$('#picked');host.innerHTML='';const rows=pickedRows();
 const cp=el('button',{className:'btn',textContent:T.copy});cp.onclick=()=>copyText([D.head,...rows].map(r=>r.join('\t')).join('\n'),cp);
 const dl=el('button',{className:'btn',textContent:T.csv});dl.onclick=()=>{const q=v=>{v=String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
  const blob=new Blob(['\ufeff'+[D.head,...rows].map(r=>r.map(q).join(',')).join('\n')],{type:'text/csv'});const a=el('a',{href:URL.createObjectURL(blob),download:D.csvName});document.body.append(a);a.click();a.remove()};
 host.append(el('div',{className:'bar2'},cp,dl));if(!rows.length){host.append(el('div',{className:'muted',textContent:T.none}));return}
 const t=el('table');t.append(el('tr',{},...D.head.map(h=>el('th',{textContent:h}))));rows.forEach(r=>t.append(el('tr',{},...r.map(v=>el('td',{className:'num',textContent:v})))));host.append(el('div',{className:'card'},t))}
function drawWafers(){const host=$('#wafers');host.innerHTML='';const t=el('table');
 t.append(el('tr',{},...[T.wafer,T.lot,T.slot,T.all,T.drop,T.keep,T.defect,T.overkill].map(h=>el('th',{textContent:h}))));
 D.wafers.forEach((w,wi)=>{const ds=D.defects.filter(d=>d.w===wi),dr=ds.filter(dropped).length;
  t.append(el('tr',{},...[w.name,w.lot||'—',w.slot||'—',ds.length,dr,ds.length-dr,ds.filter(d=>d.m===1).length,ds.filter(d=>d.m===2).length].map(v=>el('td',{className:'num',textContent:v})))) });
 host.append(el('div',{className:'card'},t))}
function drawRecipe(){const host=$('#recipe-copy');host.innerHTML='';const cp=el('button',{className:'btn',textContent:T.copy});
 cp.onclick=()=>copyText([[T.zone,T.rule,T.item,T.recipe,T.changed],...D.changes].map(r=>r.join('\t')).join('\n'),cp);host.append(cp)}
function all(){seg($('#ba'),[['before',T.before],['after',T.after]],after?'after':'before',k=>{after=k==='after';all()});drawMap();drawPhotos();drawWafers()}
$('#info').onclick=e=>{if(e.target.id==='info')e.target.style.display='none'};document.addEventListener('keydown',e=>{if(e.key==='Escape')$('#info').style.display='none'});
drawPicked();drawRecipe();all();
})();
"""


def _live_block(live: dict, changes: list) -> str:
    """눌러 보는 부분 — 자료는 JSON 한 덩어리, 그리기는 아래 스크립트(바깥 파일 없음)."""
    e = html.escape
    t = live.get("t") or live_strings()
    data = dict(live, t=t, changes=[[c.zone, c.rule, c.item, f"{c.old:.{c.dec}f}", f"{c.new:.{c.dec}f}"] for c in changes])
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    parts = [f"<style>{_LIVE_CSS}</style>", "<div id='recipe-copy' class='bar2'></div>", "<div id='ba'></div>"]
    if live.get("map"):
        parts.append(f"<h2>{e(t['map'])}</h2><div id='map' class='card'></div>")
    parts += [f"<h2>{e(t['photos'])}</h2><div id='photo-bar'></div><div id='cards' class='cards'></div>",
              f"<h2>{e(t['picked'])}</h2><div id='picked'></div>",
              f"<h2>{e(t['wafers'])}</h2><div id='wafers'></div>",
              "<div id='info'><div class='box'></div></div>",
              f"<script type='application/json' id='live-data'>{blob}</script>",
              f"<script>{_LIVE_JS}</script>"]
    return "\n".join(parts)
