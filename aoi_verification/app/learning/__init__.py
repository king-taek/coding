"""임베딩 추론 패키지 (OpenVINO 백본).

- embedder_openvino:  Intel GPU OpenVINO 임베딩 (고효율 모드 GPU 경로).
- openvino_installer: OpenVINO 자동 설치 도우미.

학습 데이터 누적/학습/평가(dataset·trainer·evaluator)와 그 산출물을 쓰던 코드
(triplet_model·embedder), 그리고 그 산출물을 가리키던 모델 레지스트리는 제거되었다.
백본 → OpenVINO IR 변환은 **빌드 때** 하고(`scripts/internal/portable_build.py`),
앱은 동봉된 IR 을 읽기만 한다 — 그래서 배포본에 torch 가 필요 없다.
"""
