"""faceoff + center-aware 런(165632) 시각화 — 속도/정확도."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = json.load(open("bench결과/20260531-165632/result.json", encoding="utf-8"))
runs = [r for r in d["runs"] if r.get("recall1") is not None]
PROD = "gpu_fusion_b16"
prod = next(r for r in runs if r["key"] == PROD)

def fam(k):
    if k == PROD: return "prod"
    if k == "cpu_classical_full": return "gold"
    if k.startswith("center_"): return "center"
    return "surv"
col = {"prod": "#1f77b4", "gold": "#9467bd", "center": "#ff7f0e", "surv": "#2ca02c"}

fig, ax = plt.subplots(figsize=(11, 7))
for r in runs:
    f = fam(r["key"])
    m = "*" if f == "prod" else ("D" if f == "gold" else "o")
    s = 320 if f == "prod" else 90
    ax.scatter(r["total_sec"], r["recall1"] * 100, c=col[f], s=s, marker=m,
               edgecolors="k", linewidths=0.5, zorder=3)
    dx = 6 if r["key"] != "rr_parallel" else 6
    ax.annotate(f"{r['key']}\n{r['total_sec']:.0f}s · {r['recall1']*100:.1f}%",
                (r["total_sec"], r["recall1"] * 100), fontsize=7.5,
                xytext=(dx, 4), textcoords="offset points")
ax.axhline(prod["recall1"] * 100, ls="--", color="#1f77b4", alpha=0.5)
ax.axvline(prod["total_sec"], ls=":", color="#1f77b4", alpha=0.4)
# 3배 목표선
ax.axvline(prod["total_sec"] / 3, ls=":", color="red", alpha=0.5)
ax.text(prod["total_sec"] / 3, 70, "  현행÷3 (3배 목표)", color="red", fontsize=9, rotation=90, va="bottom")

from matplotlib.lines import Line2D
leg = [Line2D([], [], marker="*", color="w", markerfacecolor="#1f77b4", markersize=16, label="현행 gpu_fusion_b16"),
       Line2D([], [], marker="o", color="w", markerfacecolor="#2ca02c", markersize=10, label="재채점 생존자"),
       Line2D([], [], marker="o", color="w", markerfacecolor="#ff7f0e", markersize=10, label="center-aware(신규)"),
       Line2D([], [], marker="D", color="w", markerfacecolor="#9467bd", markersize=10, label="CPU 고전 전수(gold)")]
ax.legend(handles=leg, loc="lower right", fontsize=9)
ax.set_xlabel("total time (s)  ← faster")
ax.set_ylabel("recall@1 (%)")
ax.set_title("faceoff + center-aware (2026-05-31 16:56)  |  추천=rr_parallel ×3.95 @97.6%\n"
             "center-aware(주황)는 더 느리고 정확도도 현행 이하 — GT=41(1장=2.44%p)")
ax.grid(alpha=0.3)
fig.tight_layout()
out = "bench결과/_faceoff_center_그래프.png"
fig.savefig(out, dpi=120)
print("saved", out)
