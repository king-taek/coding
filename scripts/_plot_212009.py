"""run 212009 (final preset) — NPU 고가동 가동률 + 후보 안전성."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = json.load(open("bench결과/20260531-212009/result.json", encoding="utf-8"))
runs = {r["key"]: r for r in d["runs"]}

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

# 좌: 속도-정확도 (유효 런만)
keys = ["gpu_fusion_b16", "rr_orb_center50", "cpu_rr_phash_orb", "rr_parallel",
        "cpu_rr_parallel16", "cpu_rr_orb_only", "npu_hi_conc96", "npu_hi_throughput",
        "cpu_classical_full"]
for k in keys:
    r = runs.get(k)
    if not r or r["total_sec"] <= 0 or r.get("recall1") is None:
        continue
    if k == "cpu_classical_full":
        c, m, s = "#9467bd", "D", 130
    elif k == "gpu_fusion_b16":
        c, m, s = "#1f77b4", "*", 340
    elif k == "rr_orb_center50":
        c, m, s = "#d62728", "o", 170
    elif k.startswith("npu_hi"):
        c, m, s = "#ff7f0e", "s", 110
    else:
        c, m, s = "#2ca02c", "o", 90
    ax1.scatter(r["total_sec"], r["recall1"] * 100, c=c, marker=m, s=s,
                edgecolors="k", linewidths=0.5, zorder=3)
    ax1.annotate(k, (r["total_sec"], r["recall1"] * 100), fontsize=7.5,
                 xytext=(5, 4), textcoords="offset points")
ax1.axhline(97.6, ls="--", color="#1f77b4", alpha=0.4)
ax1.set_xlabel("total time (s)  ← faster")
ax1.set_ylabel("recall@1 (%)")
ax1.set_ylim(96, 101)
ax1.set_title("final run — 추천 rr_orb_center50 14.5s ×5.72 @97.6%\n"
              "(현행 83s는 직렬 벤치 / 운영은 병렬이라 실제 더 빠름)", fontsize=11)
ax1.grid(alpha=0.3)

# 우: NPU 고가동 — 가동률 vs 결과
npu_runs = [(k, runs[k]) for k in ("npu_hi_throughput", "npu_hi_conc96",
            "npu_hi_streams4", "npu_hi_split", "npu_hi_assist_conc") if k in runs]
labels, busy, status = [], [], []
for k, r in npu_runs:
    labels.append(k.replace("npu_hi_", ""))
    busy.append((r.get("npu_busy_frac") or 0) * 100)
    if r.get("timed_out"):
        status.append("타임아웃(크래시)")
    elif r.get("fell_back_classical"):
        status.append("NPU실패→CPU폴백")
    else:
        status.append("정상 97.6%")
colors = {"정상 97.6%": "#2ca02c", "NPU실패→CPU폴백": "#ff7f0e", "타임아웃(크래시)": "#d62728"}
bars = ax2.bar(range(len(labels)), busy, color=[colors[s] for s in status])
for i, s in enumerate(status):
    ax2.text(i, busy[i] + 1, f"{busy[i]:.0f}%\n{s}", ha="center", fontsize=8)
ax2.set_xticks(range(len(labels)))
ax2.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
ax2.set_ylabel("NPU 가동률(busy %) — 프록시")
ax2.set_ylim(0, 45)
ax2.set_title("NPU 고가동 5종 — 가동률 최대 30%\n분담/보조는 타임아웃·스트림은 폴백 = NPU 강제는 불안정", fontsize=11)
ax2.grid(axis="y", alpha=0.3)

fig.tight_layout()
out = "bench결과/_run212009_그래프.png"
fig.savefig(out, dpi=120)
print("saved", out)
