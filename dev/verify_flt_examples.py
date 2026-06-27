#!/usr/bin/env python3
"""Surface.flt geometry 추출을 실측 예시(TB500_ultrafast_examples …) 전수로 검증.

목적: 앱의 **실제 코드 경로**(``surface_flt`` 파싱 → ``abs_coord`` 좌표 →
``geometry.resolve`` 환산/매칭)가 예시 문서의 정답값과 일치하는지 한 건씩 대조해
'어디서·얼마나' 어긋나는지 드러낸다. (단위 테스트는 합성/소수 고정값이라 전수
mismatch 를 못 잡는다.)

예시 문서 구조(각 ``### 예시`` 블록):
  · 6.x.4 표 : | UI 항목 | Surface.flt field | raw pixel 값 | 환산값 |  ← 정답값
  · 6.x.5    : record raw HEX (152 byte)
  · 6.x.6    : ColorImageGrabingInfo.ini 원본 section
  · coord_source / match_status 메타

검증 2단계:
  (A) raw 디코딩  : HEX 152byte 를 surface_flt 오프셋으로 풀어 문서 raw pixel 과 비교
                    → 오프셋/타입/엔디안(파싱) 검증
  (B) end-to-end : 임시 폴더에 Surface.flt(해당 record)+INI 를 복원하고 실제
                    geometry.resolve() 호출 → area_um2/width_um/length_um/contrast 를
                    문서 환산값과 비교 → 환산계수 + 좌표매칭까지 검증

사용:
    python dev/verify_flt_examples.py --examples-dir <예시 md 들이 있는 폴더>
        [--abs-tol 0.01] [--rel-tol 1e-4] [--max-show 40] [--verbose]

종료코드: 모두 통과면 0, 하나라도 불일치면 1.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

# 앱 모듈 import (repo 루트에서 실행 가정)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aoi_verification.app.coords import surface_flt, geometry, camtek_ini  # noqa: E402

_EX_RE = re.compile(r'### 예시.*?(?=### 예시|\Z)', re.S)
_HEX_RE = re.compile(r'raw HEX.*?```text\s*(.*?)```', re.S)
_INI_RE = re.compile(r'```ini\s*(.*?)```', re.S)
# 6.x.4 표의 한 행: | Area | area | <raw> | <환산값> ... |
_ROW_RE = {
    'area':           re.compile(r'\|\s*Area\s*\|\s*area\s*\|\s*([-\d.eE]+)\s*\|\s*([-\d.eE]+)'),
    'blob_breadth':   re.compile(r'\|\s*Width\s*\|\s*BlobBreadth\s*\|\s*([-\d.eE]+)\s*\|\s*([-\d.eE]+)'),
    'blob_feret_max': re.compile(r'\|\s*Length\s*\|\s*BlobFeretMax\s*\|\s*([-\d.eE]+)\s*\|\s*([-\d.eE]+)'),
    'contrast':       re.compile(r'\|\s*Contrast\s*\|\s*Contrast\s*\|\s*([-\d.eE]+)\s*\|\s*([-\d.eE]+)'),
}
_GEOM_ATTR = {  # 우리 필드 → geometry.DefectGeometry 속성 + 라벨
    'area':           ('area_um2',  'Area'),
    'blob_breadth':   ('width_um',  'Width'),
    'blob_feret_max': ('length_um', 'Length'),
    'contrast':       ('contrast',  'Contrast'),
}


def _meta(block: str, label: str):
    m = re.search(r'\|\s*' + re.escape(label) + r'\s*\|\s*([^\|]+?)\s*\|', block)
    return m.group(1).strip() if m else None


def _title(block: str):
    m = re.search(r'### 예시[^\n`]*`([^`]+)`', block)
    return m.group(1).strip() if m else '(제목없음)'


def parse_examples(paths):
    out = []
    for f in paths:
        txt = open(f, encoding='utf-8', errors='replace').read()
        for block in _EX_RE.findall(txt):
            hx = _HEX_RE.search(block)
            ini = _INI_RE.search(block)
            ex = {
                'title': _title(block),
                'file': os.path.basename(f),
                'hex': re.sub(r'\s+', '', hx.group(1)) if hx else None,
                'ini': ini.group(1).strip() if ini else None,
                'coord_source': _meta(block, 'coord_source'),
                'match_status': _meta(block, 'match_status'),
                'raw': {}, 'conv': {},
            }
            for fld, rx in _ROW_RE.items():
                m = rx.search(block)
                if m:
                    ex['raw'][fld] = float(m.group(1))
                    ex['conv'][fld] = float(m.group(2))
            out.append(ex)
    return out


def close(a, b, abs_tol, rel_tol):
    if a is None or b is None:
        return False
    return abs(a - b) <= max(abs_tol, rel_tol * abs(b))


def stem_from_ini(ini: str):
    m = re.search(r'\[([^\]]+)\]', ini)
    return Path(m.group(1)).stem if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--examples-dir', required=True,
                    help='TB500_ultrafast_examples …*part*.md 들이 있는 폴더')
    ap.add_argument('--abs-tol', type=float, default=0.01)
    ap.add_argument('--rel-tol', type=float, default=1e-4)
    ap.add_argument('--max-show', type=int, default=40)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.examples_dir, '*ultrafast_examples*part*.md')))
    if not paths:
        paths = sorted(glob.glob(os.path.join(args.examples_dir, '*part*.md')))
    if not paths:
        print(f'예시 md 를 못 찾음: {args.examples_dir}', file=sys.stderr)
        return 2

    examples = parse_examples(paths)
    print(f'스키마 활성(_SCHEMA_READY)={surface_flt._SCHEMA_READY}  '
          f'오프셋={ {k: v} if False else dict(surface_flt._FIELDS) }')
    print(f'예시 파일 {len(paths)}개, 블록 {len(examples)}건 파싱\n')

    # 통계 누적
    raw_mismatch = []     # (title, field, expected, got, diff)
    conv_mismatch = []    # PASS 인데 값/상태가 틀림
    neg_mismatch = []     # 문서 FAIL 인데 앱이 잘못 매칭(ok)
    raw_maxerr = {}
    conv_maxerr = {}
    n_raw = n_conv = 0
    n_neg = n_neg_ok = 0
    n_with_record = 0

    for ex in examples:
        if not ex['hex'] or len(ex['hex']) < 304 or not ex['raw']:
            continue
        n_with_record += 1
        rec_bytes = bytes.fromhex(ex['hex'][:304])

        # (A) raw 디코딩 — surface_flt 오프셋으로 직접 풀기
        decoded = {}
        for fld, (off, fmt) in surface_flt._FIELDS.items():
            try:
                decoded[fld] = struct.unpack_from(surface_flt._BYTE_ORDER + fmt, rec_bytes, off)[0]
            except struct.error:
                decoded[fld] = None
        for fld in ex['raw']:
            exp, got = ex['raw'][fld], decoded.get(fld)
            n_raw += 1
            d = abs((got or 0) - exp)
            raw_maxerr[fld] = max(raw_maxerr.get(fld, 0.0), d)
            if not close(got, exp, args.abs_tol, args.rel_tol):
                raw_mismatch.append((ex['title'], fld, exp, got, d))

        # (B) end-to-end — 실제 geometry.resolve()
        #   문서 match_status 에 따라 기대 동작이 다르다:
        #     PASS_COORD_EXACT  → 앱도 status='ok' + 환산값 일치해야 함
        #     FAIL_COORD_*/NO_* → 좌표가 record 와 멀거나 surface 없음 → 앱은 no_data/no_flt
        #                         (= 정상 거부. '측정정보 없음/미지원' 마커가 붙는 케이스)
        if ex['ini']:
            stem = stem_from_ini(ex['ini'])
            if stem:
                with tempfile.TemporaryDirectory() as td:
                    tdp = Path(td)
                    (tdp / 'Surface.flt').write_bytes(rec_bytes)
                    (tdp / 'ColorImageGrabingInfo.ini').write_text(
                        f'[{stem}.jpeg]\n' + ex['ini'] + '\n', encoding='utf-8')
                    img = tdp / f'{stem}.jpeg'
                    img.write_bytes(b'')
                    surface_flt.load_folder.cache_clear()
                    camtek_ini.load_abs_folder.cache_clear()
                    res = geometry.resolve(img)

                is_pass = ex['match_status'] == 'PASS_COORD_EXACT'
                if is_pass:
                    if res.status != 'ok' or res.geometry is None:
                        conv_mismatch.append((ex['title'], f'PASS인데 status={res.status}',
                                              None, None, None))
                    else:
                        for fld, (attr, _label) in _GEOM_ATTR.items():
                            if fld not in ex['conv']:
                                continue
                            exp = ex['conv'][fld]
                            got = getattr(res.geometry, attr)
                            n_conv += 1
                            d = abs(got - exp)
                            conv_maxerr[fld] = max(conv_maxerr.get(fld, 0.0), d)
                            if not close(got, exp, args.abs_tol, args.rel_tol):
                                conv_mismatch.append((ex['title'], fld, exp, got, d))
                else:
                    # 문서가 FAIL 로 판정한 케이스 — 앱도 거부(no_data/no_flt)해야 정상.
                    n_neg += 1
                    if res.status == 'ok':
                        neg_mismatch.append((ex['title'],
                                             f'문서={ex["match_status"]}인데 앱은 ok(잘못 매칭)',
                                             None, None, None))
                    else:
                        n_neg_ok += 1

    # ── 리포트 ──────────────────────────────────────────────────────────
    print('=' * 70)
    print(f'record(HEX+raw) 보유 예시: {n_with_record}건')
    print(f'\n[A] raw 디코딩 vs 문서 raw pixel  (비교 {n_raw}건)')
    for fld in _ROW_RE:
        if fld in raw_maxerr:
            print(f'    {fld:16s} max|err|={raw_maxerr[fld]:.6g}')
    print(f'    불일치 {len(raw_mismatch)}건')

    print(f'\n[B] geometry.resolve 환산값 vs 문서 환산값  '
          f'(PASS 예시만, 비교 {n_conv}건)')
    for fld in _ROW_RE:
        if fld in conv_maxerr:
            print(f'    {_GEOM_ATTR[fld][1]:8s}({fld:14s}) max|err|={conv_maxerr[fld]:.6g}')
    print(f'    불일치 {len(conv_mismatch)}건')

    print(f'\n[C] 음성 검증 — 문서가 FAIL 로 판정한 케이스 (총 {n_neg}건)')
    print(f'    앱도 올바로 거부(no_data/no_flt): {n_neg_ok}건')
    print(f'    앱이 잘못 매칭(ok): {len(neg_mismatch)}건')

    def dump(title, items):
        if not items:
            return
        print(f'\n--- {title} (상위 {min(len(items), args.max_show)}/{len(items)}) ---')
        for t, fld, exp, got, d in items[:args.max_show]:
            if exp is None:
                print(f'  {t}: {fld}')
            else:
                print(f'  {t}: {fld}  기대={exp:.6g} 실제={got:.6g} 차이={d:.6g}')

    dump('[A] raw 불일치', raw_mismatch)
    dump('[B] 환산 불일치', conv_mismatch)
    dump('[C] 음성 검증 실패(잘못 매칭)', neg_mismatch)

    ok = not raw_mismatch and not conv_mismatch and not neg_mismatch
    print('\n' + ('✅ 전부 일치 (PASS 값 일치 + FAIL 정상 거부)'
                  if ok else '❌ 불일치 존재 — 위 목록 확인'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
