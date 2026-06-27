# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — AOI 검증 앱 Windows 단독 실행형(onedir) 빌드.

빌드(반드시 Windows 에서, **저장소 루트**에서 실행 — datas 경로가 루트 기준):
    pip install -r requirements.txt pyinstaller
    pyinstaller --noconfirm scripts\internal\aoi_verification.spec
산출물: dist/AOI_Verify/AOI_Verify.exe  (폴더 통째 배포)

고효율 모드(Intel GPU 임베딩)를 위해 torch/torchvision/openvino 를 포함한다.
이 때문에 폴더 용량이 크다(대략 1.3~2.0GB).  torch 를 빼고 기본 모드만 쓰려면
아래 INCLUDE_EFFICIENCY 를 False 로.
"""

import os
from PyInstaller.utils.hooks import collect_all

INCLUDE_EFFICIENCY = True   # torch+torchvision+openvino 포함(고효율 모드)

# 이 spec 은 scripts/internal/ 에 있으므로, 저장소 루트는 두 단계 위.  PyInstaller 가
# 상대경로를 spec 폴더 기준으로 해석하는 문제를 피하려고 모두 절대경로로 만든다.
_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
_MAIN = os.path.join(_ROOT, "main.py")

# 동봉 리소스 — 스타일시트 + 엑셀 템플릿(양식.xlsx, dev/ 에 위치 → 번들 루트로).
datas = [
    (os.path.join(_ROOT, "aoi_verification", "app", "ui", "style.qss"),
     "aoi_verification/app/ui"),
    (os.path.join(_ROOT, "dev", "양식.xlsx"), "."),
]
binaries = []
hiddenimports = []

# 네이티브 DLL/데이터가 많은 패키지는 collect_all 로 모두 끌어온다.
_collect_pkgs = ["openvino", "cv2", "skimage", "scipy", "imagehash", "openpyxl",
                 "rapidocr_onnxruntime"]
if INCLUDE_EFFICIENCY:
    _collect_pkgs = ["torch", "torchvision"] + _collect_pkgs

for _pkg in _collect_pkgs:
    try:
        d, b, h = collect_all(_pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # 미설치 패키지는 건너뜀(예: INCLUDE_EFFICIENCY=False 일 때 torch).
        pass

# 빌드 군더더기 제외.  INCLUDE_EFFICIENCY=False 면 torch 계열도 제외.
excludes = [
    "tkinter", "matplotlib", "pytest", "IPython", "notebook",
    "PyQt5", "PySide6", "PySide2", "tests",
]
if not INCLUDE_EFFICIENCY:
    excludes += ["torch", "torchvision", "openvino"]


a = Analysis(
    [_MAIN],
    pathex=[_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AOI_Verify",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 앱 — 콘솔 창 숨김
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AOI_Verify",
)
