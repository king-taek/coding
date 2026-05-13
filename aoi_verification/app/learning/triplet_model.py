"""투영 헤드 (ProjectionHead) — 1차 구현에서는 백본은 frozen, 이것만 학습."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

try:  # pragma: no cover — optional
    import torch
    from torch import nn
    _HAS_TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore
    nn = None  # type: ignore
    _HAS_TORCH = False


# 기본 차원 — meta.json 의 head_dims 와 맞춤
DEFAULT_DIMS: Tuple[int, int, int] = (1280, 512, 128)


def is_available() -> bool:
    return _HAS_TORCH


if _HAS_TORCH:

    class ProjectionHead(nn.Module):
        """1280-d 백본 feature → 128-d 임베딩."""

        def __init__(self,
                     in_dim: int = DEFAULT_DIMS[0],
                     hidden: int = DEFAULT_DIMS[1],
                     out_dim: int = DEFAULT_DIMS[2]) -> None:
            super().__init__()
            self.dims = (int(in_dim), int(hidden), int(out_dim))
            self.fc1 = nn.Linear(in_dim, hidden)
            self.act = nn.ReLU(inplace=True)
            self.fc2 = nn.Linear(hidden, out_dim)

        def forward(self, x):                                   # type: ignore[override]
            return self.fc2(self.act(self.fc1(x)))

    # -------------------------------------------------------------------
    def save_head(head: "ProjectionHead", dst: Path) -> None:
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        payload = {"state_dict": head.state_dict(), "dims": list(head.dims)}
        torch.save(payload, str(dst))

    def load_head(src: Path) -> "ProjectionHead":
        """기존 가중치 파일에서 ProjectionHead 를 복원."""
        payload = torch.load(str(src), map_location="cpu")
        if isinstance(payload, dict) and "state_dict" in payload:
            dims = payload.get("dims", list(DEFAULT_DIMS))
            head = ProjectionHead(int(dims[0]), int(dims[1]), int(dims[2]))
            head.load_state_dict(payload["state_dict"])
        else:
            # 이전 포맷 호환 — bare state_dict
            head = ProjectionHead()
            head.load_state_dict(payload)  # type: ignore[arg-type]
        head.eval()
        return head

else:

    class ProjectionHead:  # type: ignore[no-redef]
        """torch 미설치 시 NOP 스텁."""

        def __init__(self, *_args, **_kwargs) -> None:
            self.dims = DEFAULT_DIMS

        def __call__(self, x):
            return x

    def save_head(*_args, **_kwargs) -> None:  # type: ignore[no-redef]
        raise RuntimeError("torch 가 설치되어 있지 않아 모델 저장 불가")

    def load_head(*_args, **_kwargs):  # type: ignore[no-redef]
        raise RuntimeError("torch 가 설치되어 있지 않아 모델 로드 불가")
