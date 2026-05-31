"""run 200149 — TOP5 후보 + 후보 recall 천장 시각화."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = json.load(open("bench결과/20260531-200149/result.json", encoding="utf-8"))
runs = [r for r in d["runs"] if r.get("recall1") is not None]
PROD = "gpu_fusion_b16"
prod = next(r for r in runs if r["key"] == PROD)
TOP5 = {"cpu_rr_phash_orb", "rr_parallel", "cpu_rr_parallel16",
        "cpu_rr_orb_only", "rr_orb_center50"}

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6.5))

# 좌: 속도-정확도, TOP5 강조
for r in runs:
    k = r["key"]
    if k == "cpu_classical_full":
        c, m, s = "#9467bd", "D", 130
    elif k == PROD:
        c, m, s = "#1f77b4", "*", 340
    elif k in TOP5:
        c, m, s = "#d62728", "o", 150
    else:
        c, m, s = "#bbbbbb", "o", 55
    ax1.scatter(r["total_sec"], r["recall1"] * 100, c=c, marker=m, s=s,
                edgecolors="k", linewidths=0.5, zorder=3)
    if k in TOP5 or k in (PROD, "cpu_classical_full"):
        ax1.annotate(k, (r["total_sec"], r["recall1"] * 100), fontsize=7.5,
                     xytext=(5, 4), textcoords="offset points")
ax1.axhline(prod["recall1"] * 100, ls="--", color="#1f77b4", alpha=0.4)
ax1.set_xlabel("total time (s)  ← faster")
ax1.set_ylabel("recall@1 (%)")
ax1.set_ylim(94, 101)
ax1.set_title("run 200149 — TOP5(빨강) 후보  |  추천 rr_orb_center50 ×4.19\n"
              "TOP5 전부 97.6%(=40/41) · 100%는 gold(210s)뿐", fontsize=11)
ax1.grid(alpha=0.3)

# 우: 후보 recall — 정답이 임베딩 순위 몇 위에(천장 설명)
ks = [5, 10, 20, 40, 100]
er = next(r for r in runs if r["key"] == PROD)["embed_recall"]
vals = [(er.get(str(k)) or 0) * 100 for k in ks]
ax2.plot(ks, vals, "o-", color="#1f77b4", lw=2, ms=8)
for k, v in zip(ks, vals):
    ax2.annotate(f"{v:.0f}%", (k, v), fontsize=9, xytext=(0, 6),
                 textcoords="offset points", ha="center")
worst = next(r for r in runs if r["key"] == PROD)["worst_correct_rank"]
ax2.axhline(97.6, ls="--", color="#d62728", alpha=0.6)
ax2.text(40, 90, f"정답 worst 순위 = {worst}\n→ topk≥{worst} 또는 전수만 100%",
         fontsize=10, color="#d62728")
ax2.set_xlabel("topk (CPU 재채점 후보 수)")
ax2.set_ylabel("후보 recall@k (%)")
ax2.set_title("임베딩 후보 recall — 정답이 몇 위에 있나\n"
              "top40 안엔 97.6%만(1장은 153위) → 검증셋 커지면 topk 키워야", fontsize=11)
ax2.set_xscale("log")
ax2.set_xticks(ks)
ax2.set_xticklabels([str(k) for k in ks])
ax2.grid(alpha=0.3)

fig.tight_layout()
out = "bench결과/_run200149_그래프.png"
fig.savefig(out, dpi=120)
print("saved", out)
