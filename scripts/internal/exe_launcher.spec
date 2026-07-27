# -*- mode: python ; coding: utf-8 -*-
"""'exe + app 폴더' 배포용 **얇은 런처** PyInstaller 스펙.

빌드(Windows, 저장소 루트에서):
    python scripts\\build.py exe          (권장 — 런타임·앱 배치까지 함께 한다)
산출물: dist\\AOI_Verify\\AOI_Verify.exe (단일 파일, 수 MB).

★ 이 exe 에는 **앱 코드가 한 줄도 들어가면 안 된다.**  PyInstaller 의 FrozenImporter 는
exe 안 PYZ 사본으로 디스크의 최신 사본을 가리므로, 앱이 exe 안에 들어가는 순간 자동
업데이트가 조용히 무력화된다(옛 단독 exe 빌드의 부분 업데이트 사고 원인).

그 보증을 **기계적으로** 만든다:
  · hiddenimports 를 비운다            — 앱 모듈을 끌어들일 통로를 없앤다
  · excludes 에 aoi_verification 명시  — 혹시 참조돼도 빠지게
  · pathex 에 저장소 루트를 넣지 않는다 — Analysis 가 앱 패키지를 찾지 못하게
빌드 후 ``build.py`` 의 verify 가 `_internal/` 부재와 exe 용량으로 이를 재확인한다.
"""

block_cipher = None

import os
# SPECPATH = 이 spec 이 있는 폴더(scripts/internal).  저장소 루트는 두 단계 위.
_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
_LAUNCHER = os.path.join(_ROOT, "scripts", "exe_launcher.py")
_ICON = os.path.join(_ROOT, "aoi_verification", "app", "ui", "assets", "logo.ico")

# ★ 비어 있어야 한다 — 런처는 표준 라이브러리만 쓴다.
hiddenimports = []
# 앱 패키지와 무거운 의존성은 명시적으로 제외(전부 app\ · python\ 에 loose 로 있다).
excludes = [
    "aoi_verification",
    "torch", "torchvision", "openvino", "cv2", "skimage", "scipy",
    "numpy", "PIL", "openpyxl", "imagehash", "rapidocr_onnxruntime",
    "PyQt6", "PySide6", "matplotlib", "tkinter", "pytest", "IPython",
]

a = Analysis(
    [_LAUNCHER],
    pathex=[],                       # ★ 저장소 루트를 넣지 않는다(앱을 못 찾게)
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="AOI_Verify",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console=False,        # GUI — 콘솔 창 없음
    icon=_ICON,                                # 탐색기/작업표시줄 아이콘
)
