/**
 * 체험판(docs/체험하기.html) 자동 검증.
 *
 *     node scripts/internal/verify_demo_html.mjs
 *
 * 체험판은 사내 PC 에서 그냥 열어 보는 파일이라 **오류가 나면 알 방법이 없다** — 열어 본
 * 사람이 그냥 닫아 버린다.  그래서 실제 브라우저로 열어 안내 투어를 처음부터 끝까지
 * 눌러 보고, 자유 조작까지 한 바퀴 돌린 뒤 콘솔 오류가 하나라도 있으면 실패로 끝낸다.
 *
 * 필요한 것: node + playwright(전역) + /opt/pw-browsers 의 chromium.  둘 다 이 환경에
 * 미리 깔려 있다.  requirements.txt 는 건드리지 않는다(배포 의존성이 아니다).
 */
import { pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import { execSync } from "node:child_process";
import path from "node:path";

/** playwright 는 보통 **전역**에 깔려 있다(이 저장소의 의존성이 아니다).
 *  ESM 은 NODE_PATH 를 보지 않으므로 전역 경로를 직접 찾아 불러온다. */
async function loadChromium() {
  try {
    return (await import("playwright")).chromium;
  } catch {
    const root = execSync("npm root -g", { encoding: "utf8" }).trim();
    const mod = path.join(root, "playwright", "index.mjs");
    if (!existsSync(mod)) {
      console.error("playwright 를 찾지 못했습니다 — `npm i -g playwright` 후 다시 실행하세요.");
      process.exit(1);
    }
    return (await import(pathToFileURL(mod).href)).chromium;
  }
}

const REPO = path.resolve(import.meta.dirname, "..", "..");
const DEMO = path.join(REPO, "docs", "체험하기.html");
const problems = [];

function watch(page, label) {
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`[${label}] console.error: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`[${label}] pageerror: ${e.message}`));
  page.on("requestfailed", (r) => {
    // 문서 안에 다 들어 있어야 한다 — 바깥으로 나가는 요청은 그 자체가 결함이다.
    problems.push(`[${label}] requestfailed: ${r.url().slice(0, 120)}`);
  });
}

async function runTour(page, label) {
  const card = page.locator('[data-test="tour-card"]');
  if (!(await card.isVisible())) await page.click('[data-test="tour-open"]');
  const total = await page.evaluate(() => STEPS.length);
  const seenChapters = new Set();
  // 동작이 있는 단계는 [해 보기] + [다음] 두 번을 누른다 — 넉넉히 잡는다.
  for (let i = 0; i < total * 2 + 12; i++) {
    if (!(await card.isVisible())) break;
    const step = await page.locator("#tour-step").textContent();
    const title = await page.locator("#tour-title").textContent();
    if (!title || !title.trim()) problems.push(`[${label}] ${step} 제목이 비었습니다`);
    // 강조 대상이 있다면 실제로 화면에 있어야 한다 — 없으면 안내가 허공을 가리킨다.
    const st = await page.evaluate(() => {
      const s = STEPS[Tour.i] || {};
      return { sel: s.sel || null, done: !!s.done, ch: s.ch };
    });
    seenChapters.add(st.ch);
    if (st.sel && !st.done) {
      const n = await page.locator(st.sel).count();
      if (n === 0) problems.push(`[${label}] ${step} 강조 대상을 찾지 못했습니다: ${st.sel}`);
    }
    const next = page.locator('[data-test="tour-next"]');
    if (!(await next.isEnabled())) await page.waitForTimeout(500);
    await next.click();
    await page.waitForTimeout(260);
  }
  if (await card.isVisible()) problems.push(`[${label}] 투어가 끝나지 않았습니다`);
  // 챕터를 하나도 빠뜨리지 않고 지나야 한다(끊긴 챕터 = 읽을 수 없는 대목).
  const chapters = await page.evaluate(() => CHAPTERS.length);
  if (seenChapters.size !== chapters)
    problems.push(`[${label}] 지나간 챕터가 ${seenChapters.size}/${chapters} 개뿐입니다`);
}

/** 목차에서 아무 챕터로나 건너뛰어도 화면과 안내가 같은 곳을 봐야 한다.
 *  (챕터마다 `seed` 가 그 대목의 시작 상태를 즉시 만든다 — 안 그러면 안내가
 *  지나간 화면을 가리킨다.) */
async function tourJumps(page, label) {
  const chapters = await page.evaluate(() => CHAPTERS.length);
  for (let ci = chapters - 1; ci >= 0; ci--) {
    await page.click('[data-test="tour-open"]');
    await page.waitForTimeout(200);
    await page.click('[data-test="tour-toc"]');
    await page.waitForSelector('[data-test="toc"]');
    await page.click(`[data-test="toc-${ci}"]`);
    await page.waitForTimeout(500);
    const got = await page.evaluate(() => ({
      ch: (STEPS[Tour.i] || {}).ch, want: (CHAPTERS[(STEPS[Tour.i] || {}).ch] || {}).page,
      page: S.page,
    }));
    if (got.ch !== ci) problems.push(`[${label}] 목차 ${ci} 로 갔는데 챕터가 ${got.ch} 입니다`);
    if (got.page !== got.want)
      problems.push(`[${label}] 챕터 ${ci} 시작 화면이 "${got.page}" 입니다(기대: "${got.want}")`);
    await page.click('[data-test="tour-skip"]');
    await page.waitForTimeout(150);
  }
}

/** 어두워진 화면(안내·로딩·시트)에서 뒤 화면이 눌리거나 키를 먹으면 안 된다.
 *  예전엔 안내 덮개가 `pointer-events:none` 이라 클릭이 그대로 통과해, 안내가
 *  가리키는 상태와 실제 화면이 갈라진 채 꼬였다. */
async function lockChecks(page, label) {
  // 1) 안내 중 — 덮개를 눌러도 뒤 화면이 반응하지 않는다.
  await page.click('[data-test="reset"]');
  await page.click('[data-test="tour-open"]');
  await page.waitForTimeout(300);
  const before = await page.evaluate(() => JSON.stringify([S.page, S.tol, S.refPath]));
  const box = await page.locator("#app").boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height - 40);
  await page.waitForTimeout(250);
  const after = await page.evaluate(() => JSON.stringify([S.page, S.tol, S.refPath]));
  if (before !== after) problems.push(`[${label}] 안내 중 어두운 화면 클릭이 뒤로 통과했습니다`);
  // 상단 체험판 줄만은 살아 있어야 한다 — 꼬였을 때의 유일한 탈출구.
  await page.click('[data-test="reset"]');
  await page.waitForTimeout(200);
  if (await page.locator('[data-test="tour-card"]').isVisible())
    problems.push(`[${label}] 안내 중 [처음부터] 가 눌리지 않습니다`);

  // 2) 로딩 중 — 키 입력이 뒤 화면으로 새지 않는다.
  for (const side of ["ref", "val"]) {
    await page.click(`[data-test="${side}-browse"]`);
    await page.click(`[data-test="folder-${side === "ref" ? "17호기" : "18호기"}"]`);
    await page.waitForTimeout(120);
  }
  await page.click('[data-test="start"]');
  await page.waitForSelector("#loading.on");
  const during = await page.evaluate(() => S.page);
  for (const k of ["ArrowRight", "ArrowLeft"]) { await page.keyboard.press(k); }
  await page.waitForTimeout(120);
  const stillLoading = await page.locator("#loading.on").count();
  const nowPage = await page.evaluate(() => S.page);
  if (stillLoading && nowPage !== during)
    problems.push(`[${label}] 로딩 중 키 입력이 뒤 화면으로 샜습니다`);
  // [처음부터] 로 진행 중인 흐름을 끊어도 옛 흐름이 화면을 덮어쓰면 안 된다.
  await page.click('[data-test="reset"]');
  await page.waitForTimeout(1800);
  const settled = await page.evaluate(() => S.page);
  if (settled !== "setup")
    problems.push(`[${label}] [처음부터] 뒤에 옛 흐름이 화면을 "${settled}" 로 덮었습니다`);
}

/** 안내 카드는 오른쪽 위 ✕ 로도, (시트가 없을 때) Escape 로도 닫힌다.
 *  Escape 순서는 목차 → 시트 → 투어 — 목차가 열려 있으면 목차만 닫는다. */
async function tourCloseChecks(page, label) {
  const card = page.locator('[data-test="tour-card"]');
  // 1) ✕ — [건너뛰기] 와 같은 일
  await page.click('[data-test="tour-open"]');
  await page.waitForTimeout(250);
  await page.click('[data-test="tour-close"]');
  await page.waitForTimeout(200);
  if (await card.isVisible()) problems.push(`[${label}] ✕ 를 눌렀는데 안내가 남아 있습니다`);
  // 2) 시트가 없으면 Escape 가 투어를 닫는다
  await page.click('[data-test="tour-open"]');
  await page.waitForTimeout(250);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  if (await card.isVisible()) problems.push(`[${label}] Escape 가 안내를 닫지 못했습니다`);
  // 3) 목차가 열려 있으면 Escape 는 목차만 닫는다 — 투어는 남는다
  await page.click('[data-test="tour-open"]');
  await page.waitForTimeout(250);
  await page.click('[data-test="tour-toc"]');
  await page.waitForTimeout(200);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  if (await page.locator("#tour-tocwrap.on").count())
    problems.push(`[${label}] Escape 가 목차를 닫지 못했습니다`);
  if (!(await card.isVisible()))
    problems.push(`[${label}] Escape 가 목차와 함께 투어까지 닫았습니다`);
  await page.click('[data-test="tour-close"]');
  await page.waitForTimeout(150);
}

async function freePlay(page, label) {
  await page.click('[data-test="reset"]');
  await page.waitForTimeout(200);
  // 폴더 두 곳 고르기
  for (const side of ["ref", "val"]) {
    await page.click(`[data-test="${side}-browse"]`);
    await page.click(`[data-test="folder-${side === "ref" ? "17호기" : "18호기"}"]`);
    await page.waitForTimeout(150);
  }
  if (await page.locator('[data-test="start"]').isDisabled())
    problems.push(`[${label}] 폴더를 둘 다 골랐는데 [검증 시작] 이 잠겨 있습니다`);

  // 다크 모드 — 검증을 시작하기 전에는 바뀌어야 한다(끝난 뒤에는 잠긴다)
  const before = await page.getAttribute("html", "data-mode");
  await page.click('[data-test="dark"]');
  await page.waitForTimeout(400);
  const after = await page.getAttribute("html", "data-mode");
  if (after === before) problems.push(`[${label}] 다크 모드가 바뀌지 않았습니다 (${before})`);
  await page.click('[data-test="dark"]');
  await page.waitForTimeout(400);

  await page.click('[data-test="start"]');
  await page.waitForSelector('[data-test="opt-none"]', { timeout: 15000 });
  await page.click('[data-test="opt-none"]');
  await page.waitForSelector('[data-page="select"]', { timeout: 15000 });

  // 시작 뒤에는 다크 모드가 잠겨야 한다 — 진행 중 상태를 보호하는 규칙
  await page.waitForTimeout(150);
  const darkAfterStart = page.locator('[data-test="dark"]');
  if ((await darkAfterStart.count()) && !(await darkAfterStart.isDisabled()))
    problems.push(`[${label}] 검증을 시작했는데 다크 모드가 잠기지 않았습니다`);

  // 1단계 — 키보드로 절반, 버튼으로 절반.
  // ★ 로딩 오버레이가 떠 있는 동안은 누르지 않는다 — 앱과 마찬가지로 입력을 막는 것이
  //   정상이므로, 여기서 그냥 누르면 '가려서 못 눌렀다'가 실패로 보고된다.
  // → = 검증(화면 배치와 같은 방향).  ← 를 누르면 제외로 가므로 이후 단언이 다른
  // 데이터로 돌아간다 — 방향을 바꿀 때 여기를 같이 고쳐야 한다.
  for (let i = 0; i < 3; i++) { await page.keyboard.press("ArrowRight"); await page.waitForTimeout(120); }
  const keptByKey = await page.locator('[data-test="right-panel"] img').count().catch(() => -1);
  // 숫자 키는 없앴다 — 눌러도 아무 일이 없어야 한다.
  const beforeNum = await page.locator('[data-test="center-img"]').getAttribute("src").catch(() => null);
  for (const k of ["1", "2"]) { await page.keyboard.press(k); await page.waitForTimeout(80); }
  const afterNum = await page.locator('[data-test="center-img"]').getAttribute("src").catch(() => null);
  if (beforeNum !== afterNum) problems.push(`[${label}] 숫자 키가 아직 동작합니다`);
  for (let i = 0; i < 14; i++) {
    if (await page.locator('[data-page="match"]').count()) break;
    if (await page.locator("#loading.on").count()) { await page.waitForTimeout(300); continue; }
    const v = page.locator('[data-test="verify"]');
    if (!(await v.count())) { await page.waitForTimeout(200); continue; }
    await v.click();
    await page.waitForTimeout(140);
  }
  await page.waitForSelector('[data-page="match"]', { timeout: 25000 });

  // 2단계 표제는 **하나**이고 모드를 따라야 한다 — 앱에서 고친 '제목 모순' 이
  // 체험판에 남아 있던 적이 있다(큰 제목 '유사도 기반' + 옆에 '좌표 기반' 동시 표시).
  {
    const titles = await page.locator('[data-page="match"] :text("Stage 2 —")').count();
    const t = await page.locator('[data-test="stage2-title"]').textContent().catch(() => "");
    if (titles !== 1) problems.push(`[${label}] 2단계 제목이 ${titles}개입니다(1개여야 함)`);
    if (!/좌표 기반/.test(t || "")) problems.push(`[${label}] 좌표 모드인데 제목이 "${t}"`);
  }

  // ★ 2단계는 **자동**이다 — 실제 앱의 자동화 수준 두 가지가 둘 다 자동 매치라
  //   (utils/prefs.py AUTO_MODES) 사람이 후보를 고르는 조작은 존재하지 않는다.
  //   그런 어포던스가 되살아나면 없는 조작을 가르치게 되므로 여기서 막는다.
  for (const gone of ['[data-test="cand"]', '[data-test="nomatch"]']) {
    if (await page.locator(gone).count())
      problems.push(`[${label}] 2단계에 사람이 매칭하는 조작이 남아 있습니다: ${gone}`);
  }
  // 아무것도 누르지 않아도 스스로 검토 화면까지 간다.
  await page.waitForSelector('[data-page="review"]', { timeout: 30000 });

  // 검토 — 차순위 교체 · 토글 · 좌우 비교
  const rows = await page.locator('[data-test="mrow"]').count();
  if (rows === 0) problems.push(`[${label}] 검토 화면에 행이 없습니다`);
  if (await page.locator('[data-test="runner"]').count())
    await page.locator('[data-test="runner"]').first().click();
  await page.waitForTimeout(200);
  await page.locator('[data-test="toggle"]').first().click();
  await page.waitForTimeout(200);
  await page.locator('[data-test="bigger"]').first().click();
  await page.waitForSelector('[data-test="sheet"]');
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  await page.click('[data-test="finish"]');
  await page.waitForSelector('[data-page="result"]', { timeout: 15000 });
  await page.click('[data-test="export"]');
  await page.waitForSelector('[data-test="export-ok"]', { timeout: 15000 });
  await page.click('[data-test="export-ok"]');
  await page.waitForTimeout(200);

  // '이럴 땐?' 안내 — 지금 화면 한 절만 펴고, 다른 화면은 탭으로 갈아 끼운다
  // (예전엔 다섯 화면을 한꺼번에 쏟아 아무도 읽지 않았다).
  await page.click('[data-test="help"]');
  await page.waitForSelector('[data-test="sheet"]');
  const tabs = await page.locator('[data-test^="help-tab-"]').count();
  if (tabs < 5) problems.push(`[${label}] 이럴 땐? 탭이 ${tabs}개뿐입니다(화면 5개)`);
  const firstRows = await page.locator('[data-test="help-rows"] .helprow').count();
  if (firstRows < 3) problems.push(`[${label}] 이럴 땐? 안내가 비었습니다 (${firstRows}줄)`);
  await page.click('[data-test="help-tab-match"]');
  await page.waitForTimeout(150);
  const matchHelp = await page.locator('[data-test="help-rows"]').textContent();
  if (!/자동|프로그램이/.test(matchHelp || ""))
    problems.push(`[${label}] 2단계 안내가 아직 사람이 고르는 것처럼 적혀 있습니다`);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
}

/** 팝업 등급 — 앱의 sheet_host 와 같아야 한다.
 *  메시지/선택 시트(chrome=False)는 제목줄도 [닫기] 도 없고, 위젯 다이얼로그는 있다. */
async function sheetShapes(page, label) {
  await page.click('[data-test="reset"]');
  await page.waitForTimeout(200);

  // 메시지 시트 — [업데이트 확인]
  await page.click('[data-test="update"]');
  await page.waitForSelector('[data-test="sheet"]');
  if ((await page.locator('[data-test="sheet"] .sheetbar').count()) !== 0)
    problems.push(`[${label}] 메시지 시트에 제목줄이 붙어 있습니다(앱은 chrome=False)`);
  if ((await page.locator('[data-test="sheet"] .msg-title').count()) !== 1)
    problems.push(`[${label}] 메시지 시트에 제목 라벨이 없습니다`);
  for (const b of ["msg-no", "msg-yes"]) {
    if (!(await page.locator(`[data-test="${b}"]`).count()))
      problems.push(`[${label}] 메시지 시트에 [${b}] 가 없습니다`);
  }
  await page.click('[data-test="msg-no"]');
  await page.waitForTimeout(300);

  // 위젯 다이얼로그 — [사진 정보 보기] 는 제목줄 + [닫기] 를 가진다
  await page.click('[data-test="imginfo"]');
  await page.waitForSelector('[data-test="sheet"]');
  if ((await page.locator('[data-test="sheet"] .sheetbar').count()) !== 1)
    problems.push(`[${label}] 위젯 시트에 제목줄이 없습니다`);
  if (!(await page.locator('[data-test="sheet-close"]').count()))
    problems.push(`[${label}] 위젯 시트에 [닫기] 가 없습니다`);
  await page.click('[data-test="sheet-close"]');
  await page.waitForTimeout(300);

  // 슬롯 선택 — 앱과 같은 구성(검색 · 전체 선택/해제 · 확인)
  await page.click('[data-test="seg-scope-sub"]');
  await page.waitForSelector('[data-test="slot-search"]');
  await page.fill('[data-test="slot-search"]', "XYA");
  await page.waitForTimeout(150);
  const hits = await page.locator('[data-test^="slot-W"]').count();
  if (hits !== 1) problems.push(`[${label}] 슬롯 검색 결과가 ${hits}개입니다(1개여야 함)`);
  await page.fill('[data-test="slot-search"]', "");
  await page.waitForTimeout(150);
  await page.click('[data-test="slot-ok"]');
  await page.waitForTimeout(300);

  // 선택지 시트(KLA) — 넷이 **대등한 타일**이고 제목줄이 없다
  for (const side of ["ref", "val"]) {
    await page.click(`[data-test="${side}-browse"]`);
    await page.click(`[data-test="folder-${side === "ref" ? "17호기" : "18호기"}"]`);
    await page.waitForTimeout(120);
  }
  await page.click('[data-test="start"]');
  await page.waitForSelector('[data-test="opt-none"]', { timeout: 15000 });
  if ((await page.locator('[data-test="sheet"] .sheetbar').count()) !== 0)
    problems.push(`[${label}] KLA 선택 시트에 제목줄이 붙어 있습니다`);
  const opts = await page.locator('[data-test^="opt-"]').count();
  if (opts !== 4) problems.push(`[${label}] KLA 선택지가 ${opts}개입니다(기준/검증/둘다/아님 4개)`);
  await page.click('[data-test="opt-none"]');
  await page.waitForSelector('[data-page="select"]', { timeout: 15000 });
}

async function main() {
  if (!existsSync(DEMO)) { console.error(`체험판이 없습니다: ${DEMO}`); process.exit(1); }
  const chromium = await loadChromium();
  const browser = await chromium.launch({ args: ["--no-sandbox"] });
  const url = pathToFileURL(DEMO).href;

  for (const [label, opts] of [
    ["1920×1080", { viewport: { width: 1920, height: 1080 } }],
    ["1366×768", { viewport: { width: 1366, height: 768 } }],
    ["동작 줄이기", { viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" }],
  ]) {
    const ctx = await browser.newContext(opts);
    const page = await ctx.newPage();
    watch(page, label);
    await page.goto(url);
    await page.waitForTimeout(400);
    // 문서에 글꼴·사진이 실제로 박혀 있는지 (빈 자산이면 화면이 통째로 달라진다)
    const assets = await page.evaluate(() => ({
      shots: Object.keys(A.shots || {}).length, fonts: Object.keys(A.fonts || {}).length,
    }));
    if (assets.shots < 10 || assets.fonts < 2)
      problems.push(`[${label}] 자산이 비었습니다 (사진 ${assets.shots}, 글꼴 ${assets.fonts}) ` +
                    `— make_demo_assets.py 를 실행하세요`);
    await runTour(page, label);
    await tourJumps(page, label);
    await tourCloseChecks(page, label);
    await lockChecks(page, label);
    await sheetShapes(page, label);
    await freePlay(page, label);
    // 가로 스크롤은 어디서도 생기면 안 된다(앱과 같은 계약)
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) problems.push(`[${label}] 가로 스크롤이 생겼습니다`);
    await ctx.close();
    console.log(`  · ${label} 통과`);
  }
  await browser.close();

  if (problems.length) {
    console.error("\n체험판 검증 실패:");
    for (const p of problems) console.error("  - " + p);
    process.exit(1);
  }
  console.log("\n체험판 검증 통과 — 콘솔 오류 0, 투어 전 단계 진행, 자유 조작 한 바퀴.");
}

main().catch((e) => { console.error(e); process.exit(1); });
