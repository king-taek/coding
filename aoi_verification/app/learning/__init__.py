"""학습/평가 기능 패키지.

- dataset:        매칭 쌍 저장소 (pairs.jsonl)
- registry:       모델 파일 목록 / active.txt / 리네임
- triplet_model:  투영 헤드 (ProjectionHead)
- embedder:       추론 wrapper (백본 + 헤드, basic 모드 지원)
- trainer:        학습 워커 (QThread)
- evaluator:      매칭 결정 로그 수집 + Hit@K 집계 + 리네임 트리거
"""
