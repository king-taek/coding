"""run 184513 — 생존자 + 중앙가중ORB + NPU보조 시각화."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = json.load(open("bench결과/20260531-184513/result.json", encoding="utf-8"))
runs = [r for r in d["runs"] if r.get("recall1") is not None]
PROD = "gpu_fusion_b16"
prod = next(r for r in runs if r["key"] == PROD)
rec = d.get("recommended_key")

def fam(k):
    if k == PROD: return "prod"
    if k == "cpu_classical_full": return "gold"
    if k.startswith("rr_orb_center") or k.startswith("rr_fusion_center"): return "orbc"
    if k.startswith("npu_assist"): return "npu"
    return "surv"
col = {"prod": "#1f77b4", "gold": "#9467bd", "orbc": "#ff7f0e", "npu": "#d62728", "surv": "#2ca02c"}
lbl = {"prod": "현행 gpu_fusion_b16", "gold": "CPU 고전 전수(100%)",
       "orbc": "중앙가중 ORB(신규)", "npu": "NPU 보조 3신호(신규)", "surv": "재채점 생존자"}

fig, ax = plt.subplots(figsize=(12, 7))
seen = set()
for r in runs:
    f = fam(r["key"])
    m = "*" if f == "prod" else ("D" if f == "gold" else "o")
    s = 340 if f == "prod" else (150 if r["key"] == rec else 90)
    ax.scatter(r["total_sec"], r["recall1"] * 100, c=col[f], s=s, marker=m,
               edgecolors=("red" if r["key"] == rec else "k"),
               linewidths=(2 if r["key"] == rec else 0.5), zorder=3,
               label=lbl[f] if f not in seen else None)
    seen.add(f)
    ax.annotate(r["key"] + (f"  ({r['total_sec']:.0f}s)"),
                (r["total_sec"], r["recall1"] * 100), fontsize=7,
                xytext=(5, -3 if f in ("surv", "orbc") else 5), textcoords="offset points")
ax.axhline(prod["recall1"] * 100, ls="--", color="#1f77b4", alpha=0.4)
ax.axvline(prod["total_sec"] / 3, ls=":", color="red", alpha=0.5)
ax.text(prod["total_sec"] / 3, 96.4, " 현행/3", color="red", fontsize=8, rotation=90, va="top")
ax.set_xlabel("total time (s)  ← faster")
ax.set_ylabel("recall@1 (%)")
ax.set_ylim(94, 101)
spd = d.get("speedup_vs_production") or 0
ax.set_title(f"run 184513 — 신규(중앙ORB·NPU보조) vs 생존자  |  추천={rec} x{spd:.2f}\n"
             "모두 97.6%(=40/41) 천장 / 신규도 동률(초과 못함) · 100%는 gold(210s)뿐",
             fontsize=11)
ax.legend(loc="lower right", fontsize=8)
ax.grid(alpha=0.3)
fig.tight_layout()
out = "bench결과/_run184513_그래프.png"
fig.savefig(out, dpi=120)
print("saved", out)
