SSS 데이터셋 실행 루트

samples/scene_NNNNNN/ : 한 장면의 설정·미리보기·SSS·annotation 전체
  sss/waterfall/       : 기본 워터폴 PNG (선택)
  sss/true_aspect/     : 실제 종횡비 PNG (선택)
  sss/raw/             : 수신 처리 전 NumPy (선택)
  sss/processed/       : 최종 dB NumPy (선택)
  annotations/masks/   : 일반 pixel mask GT (선택)
  annotations/bboxes/  : YOLO TXT + pixel JSON (선택)
manifests/scenes.jsonl : 지형·오브젝트·조사계획의 완전한 장면 기록
plan.csv/json     : 세션별 다양화 계획
index.json        : 성공/실패와 실제 session 폴더 인덱스
numerical_sampling_policy.json : 얕은 수심의 ray 계산 예산 정책
*_snapshot.json   : 실행 시작 때 고정한 설정/카탈로그
