"""임베딩 추론 패키지 (OpenVINO/CNN 백본).

- registry:           모델 파일 목록 / active.txt (기본=basic).
- triplet_model:      투영 헤드 (ProjectionHead).
- embedder:           추론 wrapper (백본 + 헤드, basic 모드 지원).
- embedder_openvino:  Intel GPU/NPU OpenVINO 임베딩 (고효율 모드 GPU 경로).
- openvino_installer: OpenVINO 자동 설치 도우미.

학습 데이터 누적/학습/평가 기능(dataset·trainer·evaluator)은 제거되었다.
"""
