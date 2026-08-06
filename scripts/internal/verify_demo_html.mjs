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
  // 동작이 있는 단계는 [해 보기] + [다음] 두 번을 누른다 — 넉넉히 잡는다.
  for (let i = 0; i < total * 2 + 8; i++) {
    if (!(await card.isVisible())) break;
    const step = await page.locator("#tour-step").textContent();
    const title = await page.locator("#tour-title").textContent();
    if (!title || !title.trim()) problems.push(`[${label}] ${step} 제목이 비었습니다`);
    // 강조 대상이 있다면 실제로 화면에 있어야 한다 — 없으면 안내가 허공을 가리킨다.
    const st = await page.evaluate(() => {
      const s = STEPS[Tour.i] || {};
      return { sel: s.sel || null, done: !!s.done };
    });
    if (st.sel && !st.done) {
      const n = await page.locator(st.sel).count();
      if (n === 0) problems.push(`[${label}] ${step} 강조 대상을 찾지 못했습니다: ${st.sel}`);
    }
    const next = page.locator('[data-test="tour-next"]');
    if (!(await next.isEnabled())) await page.waitForTimeout(400);
    await next.click();
    await page.waitForTimeout(260);
  }
  if (await card.isVisible()) problems.push(`[${label}] 투어가 끝나지 않았습니다`);
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

  // 2단계 — 후보 클릭과 매칭 없음 섞기
  for (let i = 0; i < 16; i++) {
    if (await page.locator('[data-page="review"]').count()) break;
    if (await page.locator("#loading.on").count()) { await page.waitForTimeout(300); continue; }
    if (i % 3 === 2 && (await page.locator('[data-test="nomatch"]').count()))
      await page.click('[data-test="nomatch"]');
    else if (await page.locator('[data-test="cand"]').count())
      await page.locator('[data-test="cand"]').first().click();
    else { await page.waitForTimeout(200); continue; }
    await page.waitForTimeout(150);
  }
  await page.waitForSelector('[data-page="review"]', { timeout: 25000 });

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

  // '이럴 땐?' 안내 — 화면마다 열리고 내용이 있어야 한다.
  for (const page_ of ["result"]) {
    await page.click('[data-test="help"]');
    await page.waitForSelector('[data-test="sheet"]');
    const rows = await page.locator('[data-test="sheet"] h3').count();
    if (rows < 3) problems.push(`[${label}] 이럴 땐? 안내가 비었습니다 (${rows}절)`);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
  }
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
