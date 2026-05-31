# -*- mode: python ; coding: utf-8 -*-
"""온라인 다운로드형 **작은 launcher** PyInstaller 스펙.

빌드(Windows, 저장소 루트에서):
    pyinstaller --noconfirm scripts\internal\online.spec
산출물: dist\AOI_Verify_Online.exe (단일 파일, 수십 MB).

이 exe 는 앱 소스/무거운 의존성(torch·openvino)을 **포함하지 않는다**.  처음 실행 시
GitHub 에서 앱을 받아 %LOCALAPPDATA%\\AOI_Verify 에 풀고 인터넷으로 pip 설치한 뒤 실행한다.
따라서 동봉할 것은 launcher 가 import 하는 부트스트랩/업데이터 모듈뿐이다.
"""

block_cipher = None

# launcher 가 실제로 import 하는 최소 모듈만 동봉(작게 유지).
hiddenimports = [
    "aoi_verification",
    "aoi_verification.app",
    "aoi_verification.app.utils",
    "aoi_verification.app.utils.bootstrap",
    "aoi_verification.app.utils.updater",
    "aoi_verification.app.utils.paths",
]
# 무거운 패키지는 명시적으로 제외(혹시 끌려와도 빠지게) — 온라인 설치 대상.
excludes = [
    "torch", "torchvision", "openvino", "cv2", "skimage", "scipy",
    "PyQt6", "PySide6", "matplotlib", "tkinter", "pytest", "IPython",
]

a = Analysis(
    ["scripts/launcher.py"],
    pathex=[],
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
    name="AOI_Verify_Online",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    runtime_tmpdir=None, console=False,        # GUI 앱이라 콘솔 숨김
)
