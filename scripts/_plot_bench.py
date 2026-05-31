"""bench결과 시각화 — 속도-정확도 파레토 + 재채점 단축 + ORB 영향 + NPU 배치 붕괴.
일회성 분석 스크립트(레포 산출물 아님).  라벨은 ASCII 로(폰트 깨짐 방지)."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1] / "bench결과"
fast = json.load(open(ROOT / "20260531-152229" / "result.json", encoding="utf-8"))
emb = json.load(open(ROOT / "20260531-150236" / "result.json", encoding="utf-8"))
npu = json.load(open(ROOT / "20260531-160221" / "result.json", encoding="utf-8"))

PROD_T, PROD_A = 62.1, 0.9756   # 현행 gpu_fusion_b16 (어제 정규 런)

def runs(d):
    return [r for r in d["runs"] if r.get("recall1") is not None and r["total_sec"] > 0]

ORB_DROP = {"rr_phash_topk20", "cpu_rr_phash", "rr_phash", "rr_npu_phash_parallel",
            "cpu_rr_phash_ssim", "rr_phash_ssim", "cpu_rr_ssim_only"}

fig = plt.figure(figsize=(15, 11))
fig.suptitle("AOI matching speedup benchmark (2026-05-31)  |  GT=41 queries, 1 img = 2.44%p",
             fontsize=14, fontweight="bold")

# ---- Panel 1: speed-accuracy Pareto (fast-rerank run) ----
ax = fig.add_subplot(2, 2, 1)
for r in runs(fast):
    drop = r["key"] in ORB_DROP
    c = "#d62728" if drop else "#2ca02c"
    ax.scatter(r["total_sec"], r["recall1"] * 100, s=60, color=c, zorder=3,
               edgecolors="k", linewidths=0.4)
    if r["key"] in {"rr_parallel", "cpu_rr_orb_only", "cpu_rr_topk20",
                    "rr_npu_phash_parallel", "cpu_rr_topk10", "cpu_classical_full"}:
        ax.annotate(r["key"], (r["total_sec"], r["recall1"] * 100),
                    fontsize=8, xytext=(5, 4), textcoords="offset points")
ax.scatter([PROD_T], [PROD_A * 100], marker="*", s=320, color="#1f77b4",
           edgecolors="k", zorder=4, label="gpu_fusion_b16 (current)")
ax.axhline(PROD_A * 100, ls="--", color="#1f77b4", alpha=0.5)
ax.set_xlabel("total time (s)  ← faster")
ax.set_ylabel("recall@1 (%)")
ax.set_title("1) Speed vs Accuracy  (green=ORB kept, red=ORB dropped)")
ax.legend(loc="lower right", fontsize=8)
ax.grid(alpha=0.3)

# ---- Panel 2: rerank time cut (top survivors vs current) ----
ax = fig.add_subplot(2, 2, 2)
keys = ["gpu_fusion_b16\n(current)", "cpu_rr_parallel16", "cpu_rr_orb_only",
        "cpu_rr_phash_orb", "rr_parallel"]
score = [58.2, 17.0, 13.7, 13.5, 13.4]
acc = [97.6, 97.6, 97.6, 97.6, 97.6]
bars = ax.barh(keys, score, color="#2ca02c")
bars[0].set_color("#1f77b4")
for b, s, a in zip(bars, score, acc):
    ax.text(s + 1, b.get_y() + b.get_height() / 2, f"{s:.0f}s  ({a:.1f}%)",
            va="center", fontsize=9)
ax.invert_yaxis()
ax.set_xlabel("rerank (CPU re-score) time (s)")
ax.set_title("2) Bottleneck cut: 58s → ~13s, accuracy held (97.6%)")
ax.set_xlim(0, 70)
ax.grid(axis="x", alpha=0.3)

# ---- Panel 3: ORB kept vs dropped (accuracy) ----
ax = fig.add_subplot(2, 2, 3)
kept = [r["recall1"] * 100 for r in runs(fast)
        if r["key"] not in ORB_DROP and r["key"] != "cpu_classical_full"]
drop = [r["recall1"] * 100 for r in runs(fast) if r["key"] in ORB_DROP]
ax.boxplot([kept, drop], labels=[f"ORB kept (n={len(kept)})", f"ORB dropped (n={len(drop)})"],
           patch_artist=True,
           boxprops=dict(facecolor="#cdeccd"), medianprops=dict(color="k"))
for i, vals in enumerate([kept, drop], start=1):
    ax.scatter([i] * len(vals), vals, color=["#2ca02c", "#d62728"][i - 1],
               zorder=3, alpha=0.7)
ax.axhline(97.6, ls="--", color="#1f77b4", alpha=0.6, label="current 97.6%")
ax.set_ylabel("recall@1 (%)")
ax.set_title("3) ORB is accuracy-critical (dropping it collapses recall)")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3)

# ---- Panel 4: NPU batch/concurrency accuracy collapse ----
ax = fig.add_subplot(2, 2, 4)
nr = sorted(runs(npu), key=lambda r: r["recall1"], reverse=True)
labels = [r["key"] for r in nr]
vals = [r["recall1"] * 100 for r in nr]
colors = ["#2ca02c" if r["key"] == "npu_b1" else "#d62728" for r in nr]
ax.bar(range(len(vals)), vals, color=colors)
ax.set_xticks(range(len(labels)))
ax.set_xticklabels(labels, rotation=70, ha="right", fontsize=7)
ax.axhline(97.6, ls="--", color="#1f77b4", alpha=0.6)
ax.set_ylabel("recall@1 (%)")
ax.set_ylim(0, 105)
ax.set_title("4) NPU embedding: only batch=1 holds accuracy (batching corrupts it)")
ax.grid(axis="y", alpha=0.3)

fig.tight_layout(rect=[0, 0, 1, 0.97])
out = Path(__file__).resolve().parents[1] / "bench결과" / "_분석그래프.png"
fig.savefig(out, dpi=120)
print("saved", out)
