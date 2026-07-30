"""docs/exe_만들기_가이드.pdf 생성 — 빌드하는 사람에게 주는 한 장짜리 안내.

내용을 고쳤으면 이 스크립트를 다시 돌려 PDF 를 갱신한다(PDF 만 고칠 수 없다).

    python scripts/internal/make_exe_guide_pdf.py

필요한 것 (이 스크립트 전용 — requirements.txt 에 넣지 않는다):
    pip install reportlab
    한글 글꼴: NanumGothic (Windows 는 아래 FONTS 를 맑은 고딕 경로로 바꿔도 된다)
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer,
    Table, TableStyle,
)

FONTS = Path("/usr/share/fonts/truetype/nanum")
pdfmetrics.registerFont(TTFont("Nanum", FONTS / "NanumGothic.ttf"))
pdfmetrics.registerFont(TTFont("NanumB", FONTS / "NanumGothicBold.ttf"))
pdfmetrics.registerFont(TTFont("Mono", FONTS / "NanumGothicCoding.ttf"))
pdfmetrics.registerFont(TTFont("MonoB", FONTS / "NanumGothicCodingBold.ttf"))
pdfmetrics.registerFontFamily("Nanum", normal="Nanum", bold="NanumB")
pdfmetrics.registerFontFamily("Mono", normal="Mono", bold="MonoB")

INK = colors.HexColor("#1a1d24")
MUTE = colors.HexColor("#5b6472")
ACCENT = colors.HexColor("#0b6bcb")
WARN = colors.HexColor("#b54708")
RULE = colors.HexColor("#dfe3e8")
CODEBG = colors.HexColor("#f4f6f8")
WARNBG = colors.HexColor("#fff6ed")
OKBG = colors.HexColor("#eef6ff")

def _s(name, **kw):
    base = dict(name=name, fontName="Nanum", fontSize=10, leading=16,
                textColor=INK, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(**base)


S = {
    "title": _s("title", fontName="NanumB", fontSize=24, leading=31,
                spaceAfter=4),
    "subtitle": _s("subtitle", fontSize=10.5, leading=16, textColor=MUTE,
                   spaceAfter=16),
    "h1": _s("h1", fontName="NanumB", fontSize=15, leading=21,
             spaceBefore=17, spaceAfter=7, textColor=ACCENT),
    "h2": _s("h2", fontName="NanumB", fontSize=11.5, leading=18,
             spaceBefore=12, spaceAfter=4),
    "body": _s("body", spaceAfter=6),
    "bullet": _s("bullet", leftIndent=13, bulletIndent=3, spaceAfter=3),
    "note": _s("note", fontSize=9.5, leading=15, textColor=MUTE, spaceAfter=6),
    "code": _s("code", fontName="Mono", fontSize=9, leading=14.5,
               textColor=INK),
    "cell": _s("cell", fontSize=9, leading=14),
    "cellb": _s("cellb", fontName="NanumB", fontSize=9, leading=14),
    "cellh": _s("cellh", fontName="NanumB", fontSize=9, leading=14,
                textColor=colors.white),
    "callout": _s("callout", fontSize=9.5, leading=15.5),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def bullets(items, style="bullet"):
    return [Paragraph(t, S[style], bulletText="•") for t in items]


def code(lines, caption=None):
    """코드 블록 — 회색 배경 + 등폭 글꼴."""
    body = "<br/>".join(lines)
    t = Table([[Paragraph(body, S["code"])]], colWidths=[163 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODEBG),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    out = [t, Spacer(1, 7)]
    if caption:
        out.insert(0, Paragraph(caption, S["note"]))
    return out


def callout(title, lines, kind="info"):
    bg, bar = (WARNBG, WARN) if kind == "warn" else (OKBG, ACCENT)
    inner = [Paragraph(f'<font color="{bar.hexval()}"><b>{title}</b></font>',
                       S["callout"])]
    inner += [Paragraph(t, S["callout"]) for t in lines]
    t = Table([[inner]], colWidths=[163 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 2.6, bar),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [t, Spacer(1, 8)]


def table(rows, widths, header=True):
    data = []
    for r_i, row in enumerate(rows):
        st = "cellh" if (header and r_i == 0) else "cell"
        data.append([Paragraph(c, S[st]) for c in row])
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3d4757"))]
        for i in range(2, len(rows), 2):
            style.append(("BACKGROUND", (0, i), (-1, i),
                          colors.HexColor("#fafbfc")))
    t.setStyle(TableStyle(style))
    return [t, Spacer(1, 9)]


def step(n, title):
    """번호가 붙은 큰 단계 제목."""
    num = Table([[Paragraph(f'<font color="white"><b>{n}</b></font>',
                            S["cellb"])]], colWidths=[7.5 * mm],
                rowHeights=[7.5 * mm])
    num.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    row = Table([[num, Paragraph(title, S["h2"])]],
                colWidths=[10 * mm, 153 * mm])
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return row


# ---------------------------------------------------------------------------
# 문서 내용
# ---------------------------------------------------------------------------
story = []

story.append(P("AOI 검증 프로그램 — exe 만들기", "title"))
story.append(P("파이썬이 없는 PC 에 전달하는 배포본을 만드는 방법. "
               "빌드하는 사람이 처음부터 끝까지 따라 하면 됩니다.", "subtitle"))

story += callout("한 줄 요약", [
    "빌드 PC(Windows + 인터넷)에서 명령 <b>두 줄</b>이면 끝납니다. "
    "결과물은 zip 파일 하나이고, 받는 사람은 압축을 풀고 exe 를 더블클릭합니다.",
])

story.append(P("0. 준비물", "h1"))
story += table([
    ["항목", "내용"],
    ["운영체제", "<b>Windows</b> 10/11 — PyInstaller 는 다른 OS 에서 대신 만들 수 "
                 "없습니다"],
    ["파이썬", "3.10 이상 (python.org 공식 설치본). 빌드 PC 에만 필요하고, "
               "받는 사람에게는 필요 없습니다"],
    ["인터넷", "필요합니다. 런타임·패키지·모델 파일을 내려받습니다"],
    ["디스크", "<b>10 GB 이상</b> 여유. 모자라면 30분쯤 뒤 설치 도중에 멈춥니다"],
    ["시간", "<b>30분 이상</b> — 처음 한 번이 가장 오래 걸립니다"],
], [30 * mm, 133 * mm])

story += callout("먼저 확인하세요 — 회사 보안 정책", [
    "빌드 PC 에 회사가 금지한 패키지가 깔려 있으면 빌드가 <b>중단</b>됩니다. "
    "무거운 설치를 시작하기 전에 먼저 검사하므로 30분을 버리지는 않습니다.",
    "중단되면 화면의 <b>[금지]</b> 메시지가 <b>설치 위치와 제거 명령을 그대로 "
    "알려줍니다.</b> 그 명령을 복사해 실행한 뒤 다시 빌드하세요.",
], kind="warn")

story.append(P("1. 빌드하기", "h1"))

story.append(step(1, "저장소를 최신으로 맞춘다"))
story += code([
    "cd C:\\경로\\coding",
    "git pull",
])
story += callout("커밋하지 않은 변경이 있으면 경고가 뜹니다", [
    "배포본에는 현재 커밋 번호가 찍히는데, 실제로 복사되는 것은 작업 중인 파일입니다. "
    "그러면 사용자 앱이 <b>“최신입니다”</b> 라며 공개되지 않은 코드를 계속 "
    "실행합니다. <b>커밋·푸시한 뒤 빌드</b>하는 것을 권합니다.",
], kind="warn")

story.append(step(2, "빌드 명령을 실행한다"))
story += code([
    "python scripts\\build.py exe",
])
story.append(P("30분 이상 걸립니다. 화면 버퍼를 넘겨 원인을 놓치기 쉬우니 "
               "<b>로그를 파일로 남기는 것</b>을 권합니다:", "body"))
story += code([
    "python scripts\\build.py exe 2>&amp;1 | Tee-Object -FilePath dist\\build.log",
])
story.append(P("실패하면 이 <font face=\"Mono\">build.log</font> 파일만 보내면 "
               "어느 단계에서 멈췄는지 알 수 있습니다. "
               "단, 로그를 <font face=\"Mono\">dist\\AOI_Verify\\</font> "
               "<b>안에는 두지 마세요</b> — 배포 zip 에 딸려 들어갑니다.", "note"))

story.append(P("빌드가 하는 일 (순서대로)", "h2"))
story += table([
    ["단계", "내용"],
    ["① 사전 점검", "디스크 여유 · 회사 보안 정책 · 옛 산출물 정리"],
    ["② 런처 exe", "얇은 exe 를 만듭니다(수 MB). <b>앱 코드는 한 줄도 안 들어갑니다</b>"],
    ["③ 런타임", "자체 포함 CPython 을 받아 풉니다"],
    ["④ 의존성", "번들 파이썬에 PyQt6 · OpenVINO 등을 설치합니다"],
    ["⑤ 모델 IR", "검사에 쓰는 백본을 OpenVINO 형식으로 미리 변환해 동봉합니다"],
    ["⑥ 앱 복사", "앱 소스 · 리소스 · 양식.xlsx · 버전 정보"],
    ["⑦ 검증", "빠진 것이 없는지 항목별로 확인합니다"],
], [26 * mm, 137 * mm])

story.append(P("마지막에 이렇게 나오면 성공입니다 "
               "(항목 수는 버전에 따라 다를 수 있습니다):", "body"))
story += code([
    "[verify] 결과: 21/21 통과 — 빌드 정상!",
    "[다음] 배포용 zip 만들기:  python scripts\\make_release_zip.py",
])

story.append(step(3, "배포용 zip 을 만든다"))
story += code([
    "python scripts\\make_release_zip.py",
])
story.append(P("결과물: <font face=\"Mono\"><b>dist\\AOI_Verify_날짜_커밋7자리"
               ".zip</b></font> — <b>이 파일 하나만 전달</b>하면 됩니다. "
               "이름에 날짜와 커밋이 박혀 있어 누구에게 어느 버전을 줬는지 나중에 "
               "추적할 수 있습니다.", "body"))
story += callout("검증을 통과하지 못하면 zip 을 만들지 않습니다", [
    "깨진 배포본을 사용자에게 보내는 사고를 여기서 막습니다. "
    "화면에 <b>[!!]</b> 로 표시된 항목과 <b>[진단]</b> 안내를 따라 조치한 뒤 "
    "다시 실행하세요.",
])

lite = [P("1-B. 전달 파일을 줄이고 싶다면 — exe-lite", "h1")]
lite.append(P("라이브러리를 빼고, 사용자 PC 가 <b>첫 실행 때 인터넷으로 받게</b> 하는 "
              "방식입니다. 나머지는 위와 완전히 같습니다 — 파이썬과 검사용 모델은 그대로 "
              "동봉되므로 사용자 PC 에 파이썬이 필요 없고 GPU 가속도 동일합니다.", "body"))
lite += code([
    "python scripts\\build.py exe-lite",
    "python scripts\\make_release_zip.py --lite",
])
lite.append(P("산출물은 <font face=\"Mono\">dist\\AOI_Verify_Lite\\</font> 로 "
              "<b>따로</b> 만들어집니다. 전체 배포본과 함께 가지고 있을 수 있습니다.",
              "note"))
lite += callout("이 방식을 쓸 때 반드시 아셔야 하는 것", [
    "<b>각 PC 가 첫 실행 때 인터넷(PyPI)에 닿아야 합니다.</b> 닿지 못하면 그 PC 는 "
    "앱을 아예 켤 수 없습니다.",
    "총 다운로드 양이 주는 것이 아니라 <b>‘들고 가는 파일’ 이 작아지는 것</b>입니다 — "
    "라이브러리는 각 PC 가 따로 받습니다.",
    "그래서 <b>전체 배포본을 없애지 마세요.</b> 인터넷이 막힌 PC 나 설치가 실패한 PC "
    "에는 전체 배포본을 주면 됩니다.",
], kind="warn")
lite.append(P("사용자 첫 실행은 이렇게 보입니다", "h2"))
lite += bullets([
    "exe 를 더블클릭하면 <b>검은 창이 뜨고</b> 글자가 올라갑니다 — 설치 중이며 "
    "몇 분 걸립니다.",
    "끝나면 앱 창이 저절로 뜹니다. <b>두 번째 실행부터는 검은 창 없이</b> 바로 켜집니다.",
    "zip 안의 <font face=\"Mono\">설치방법.txt</font> 에 '창을 닫지 마세요' 안내가 "
    "들어갑니다.",
])
story.append(KeepTogether(lite))

_N = "&nbsp;"          # HTML 은 연속 공백을 접는다 — 트리 들여쓰기는 이걸로
tree = [P("2. 만들어진 것 — 폴더 구조", "h1")]
tree += code([
    "AOI_Verify\\",
    _N * 2 + "AOI_Verify.exe" + _N * 6 + "← 이것을 더블클릭 (얇은 런처, 수 MB)",
    _N * 2 + "app\\" + _N * 16 + "← ★ 자동 업데이트가 바꾸는 전부",
    _N * 2 + "python\\" + _N * 13 + "← 번들 CPython + 의존성 (무겁고 거의 안 바뀜)",
    _N * 2 + "runtime\\ir\\" + _N * 9 + "← 검사용 모델(미리 변환해 동봉)",
    _N * 2 + "결과\\" + _N * 16 + "← 엑셀 출력이 쌓이는 곳",
    _N * 2 + "run_aoi.bat" + _N * 9 + "← exe 가 백신에 막힐 때의 대체 실행",
    _N * 2 + "run_aoi_debug.bat" + _N * 3 + "← 오류 확인용 (콘솔 + 메시지)",
    _N * 2 + "설치방법.txt" + _N * 9 + "← 받는 사람용 안내 (zip 안에 자동 포함)",
])
story.append(KeepTogether(tree))

story += callout("왜 앱을 exe 안에 넣지 않나요", [
    "예전 방식은 앱 전체를 exe 안에 얼려 넣었습니다. 그러면 자동 업데이트가 새 파일을 "
    "디스크에 잘 써 넣어도 <b>실행 중인 파이썬은 exe 안의 옛 코드를 읽습니다.</b> "
    "“업데이트되었습니다” 라고 하고도 바뀌지 않던 원인이 이것입니다.",
    "지금은 바뀌는 것이 전부 <font face=\"Mono\">app\\</font> 폴더에 <b>파일로</b> "
    "놓여 있고, exe 에는 앱 코드가 한 줄도 없습니다.",
])

recv = [P("3. 받는 사람 안내", "h1")]
recv += bullets([
    "받은 zip 을 <b>짧은 경로</b>에 압축 해제 (예: <font face=\"Mono\">C:\\AOI_Verify</font>). "
    "한글이 섞인 깊은 경로는 Windows 260자 제한에 걸릴 수 있습니다.",
    "<font face=\"Mono\">AOI_Verify\\AOI_Verify.exe</font> 를 더블클릭. "
    "파이썬 설치는 필요 없습니다.",
    "첫 실행에 <b>“알 수 없는 게시자”</b> 경고가 뜨면 → <b>추가 정보 → 실행</b>. "
    "(서명되지 않은 exe 라서 그렇습니다.)",
    "앱이 안 켜지면 <font face=\"Mono\">run_aoi_debug.bat</font> 로 콘솔에 뜨는 "
    "메시지를 확인해 알려달라고 하세요.",
])
recv.append(P("자세한 내용은 zip 안의 <font face=\"Mono\">설치방법.txt</font> 에 "
               "들어 있습니다.", "note"))
story.append(KeepTogether(recv))

story.append(P("4. 다음부터는 zip 을 다시 안 줘도 됩니다", "h1"))
story.append(P("앱이 시작할 때 GitHub 를 확인해 새 버전이 있으면 "
               "<font face=\"Mono\">app\\</font> 폴더만 갱신합니다. 무거운 "
               "<font face=\"Mono\">python\\</font> 은 그대로 두므로 수 MB 만 오갑니다.", "body"))
story += bullets([
    "실행 중에는 받아두기만 하고, <b>다음 실행 때 런처가 교체</b>합니다.",
    "교체가 중간에 끊겨도 복구하며, <b>어떤 실패든 구버전이 살아남습니다.</b>",
    "<font face=\"Mono\">결과\\</font> 폴더는 <font face=\"Mono\">app\\</font> "
    "바깥이라 업데이트에 쓸려 나가지 않습니다.",
])

story += callout("예외 — 패키지가 바뀌었을 때는 zip 을 다시 줘야 합니다", [
    "새 패키지가 필요한 변경은 앱이 직접 설치를 시도하고, <b>설치가 성공해야만</b> "
    "적용합니다. 사용자 PC 가 인터넷에 닿지 않아 설치가 실패하면 업데이트가 "
    "적용되지 않습니다.",
    "그때는 <b>다시 빌드해서 <font face=\"Mono\">AOI_Verify</font> 폴더 전체를 "
    "zip 으로</b> 전달하세요. "
    "<b><font face=\"Mono\">app</font> 폴더만 전달하면 앱이 실행되지 않습니다.</b>",
], kind="warn")

trouble = [P("5. 막혔을 때", "h1")] + table([
    ["증상", "해결"],
    ["<b>[금지]</b> 메시지와 함께 빌드가 멈춤",
     "회사가 금지한 패키지가 빌드 PC 에 있습니다. 메시지가 알려주는 제거 명령을 "
     "그대로 실행한 뒤 다시 빌드하세요."],
    ["디스크 공간 부족으로 멈춤",
     "10 GB 이상 확보 후 재실행. 무거운 <font face=\"Mono\">python\\</font> 은 "
     "재사용하므로 처음부터 다시 받지 않습니다."],
    ["다운로드가 실패하거나 파일이 너무 작다고 나옴",
     "회사 프록시가 차단 페이지를 돌려준 경우입니다. 안내에 나온 주소를 브라우저로 "
     "직접 열어 받아지는지 확인하세요."],
    ["검증에서 <b>[!!]</b> 가 뜸",
     "<b>[진단]</b> 줄이 다음에 할 일을 알려줍니다. 대개 다시 빌드하면 해결됩니다."],
    ["옛날 방식으로 빌드한 적이 있음",
     "그대로 두어도 됩니다. 빌드가 시작할 때 옛 산출물을 자동으로 정리합니다."],
    ["exe 가 회사 백신에 막힘",
     "같은 폴더의 <font face=\"Mono\">run_aoi.bat</font> 으로 실행하면 됩니다. "
     "동작은 같습니다."],
], [52 * mm, 111 * mm])
story.append(KeepTogether(trouble))

story.append(P("참고 문서 (저장소 안)", "h1"))
story += bullets([
    "<font face=\"Mono\">docs/exe_설치_가이드.md</font> — 배포 방식 네 가지 비교, "
    "기존 사용자 이관 방법",
    "<font face=\"Mono\">docs/업데이트_동작.md</font> — 자동 업데이트가 "
    "무엇을 바꾸고 무엇을 안 바꾸는지",
    "<font face=\"Mono\">README.md</font> — 개발 환경 설정과 앱 사용법",
])


# ---------------------------------------------------------------------------
# 페이지 틀 (머리말/꼬리말)
# ---------------------------------------------------------------------------
def decorate(canv, doc):
    canv.saveState()
    w, h = A4
    canv.setFont("Nanum", 8)
    canv.setFillColor(MUTE)
    canv.drawString(23 * mm, h - 12 * mm, "AOI 검증 프로그램 — exe 만들기")
    canv.setStrokeColor(RULE)
    canv.setLineWidth(0.5)
    canv.line(23 * mm, h - 14 * mm, w - 23 * mm, h - 14 * mm)
    canv.line(23 * mm, 15 * mm, w - 23 * mm, 15 * mm)
    canv.drawCentredString(w / 2, 10 * mm, str(canv.getPageNumber()))
    canv.restoreState()


out = Path(__file__).resolve().parents[2] / "docs" / "exe_만들기_가이드.pdf"
doc = BaseDocTemplate(str(out), pagesize=A4,
                      leftMargin=23 * mm, rightMargin=23 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm,
                      title="AOI 검증 프로그램 — exe 만들기",
                      author="AOI Verification")
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                   onPage=decorate)])
doc.build(story)
print("생성:", out, out.stat().st_size, "bytes")
