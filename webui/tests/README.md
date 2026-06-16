# webui 작업 중단/정리 테스트

검은 화면 버그(처리 중 change-source/새로고침 후 새 작업 → 검은 화면) 수정과
연관 동작을 검증한다.

## 무엇을 고쳤나
- **백스톱**: 새 소스(`/upload`, `/rtsp`)가 들어오면 다른 모든 실행 중 작업(파일/라이브)을
  `stop` 처리 → 단일 사용자 환경에서 옛 작업이 모델 락을 즉시 놓는다. (`_stop_others`)
- **파일 작업 중단 가능**: `_worker` 루프가 `job.stop`을 검사해 즉시 중단하고 `stopped` 상태로.
  부분 결과물(mp4)·replay 버퍼는 폐기. (이전엔 파일 작업이 영상 끝까지 락을 점유)
- **즉시 회수**: 프론트 `beforeunload`→`sendBeacon('/stop')`(새로고침·탭종료),
  `change source`→`/stop` 으로 GPU를 곧바로 반납. (못 잡아도 백스톱이 다음 소스에서 정리)
- **메모리 해제**: 완료 작업의 `replay_frames`/`replay_metrics`를 새 소스 진입 시 비움.
- **다운로드 차단**: 중단/오류 작업은 `/result` 가 409.

## 자동 테스트
서버를 띄운 뒤:
```bash
python -m webui                       # 터미널 1: 서버 (TRT 로드까지 잠깐 대기)
# 짧은 클립(25프레임) 준비 — T3용
ffmpeg -y -loglevel error -i assets/sample1.mp4 -frames:v 25 /tmp/clip_short.mp4
python webui/tests/test_stop_recovery.py   # 터미널 2
```
T1(백스톱) / T2(파일 /stop + 다운로드 차단) / T3(replay 메모리 해제) 각각 PASS/FAIL 출력,
하나라도 실패하면 종료코드 1.

## 수동 브라우저 테스트 (http://localhost:8000)
1. **검은 화면 재현→해결**: 영상 업로드→기본 시각화로 분석 시작→처리 중
   `↻ Change source`→**같은 영상 다시 업로드**→분석 시작.
   → 검은 화면 없이 새 영상이 바로 보여야 함.
2. **새로고침**: 분석 처리 중 브라우저 새로고침(F5)→새 영상 업로드/분석.
   → 막힘 없이 진행돼야 함(옛 작업은 서버에서 회수됨).
3. **탭 종료**: 처리 중 탭을 닫았다가 다시 열어 새 작업 시작 → 정상 진행.
4. **정상 다운로드 회귀**: 기본 시각화를 끝까지 완료 → 다운로드 버튼으로 H.264 mp4 정상 저장.
5. **RTSP**: 라이브 스트림 중 정지/Change source/새로고침 후 새 소스 → 정상 전환.
