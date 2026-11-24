// pm2 구성 — 운영 서버의 프로세스 전체를 한 파일로 재현한다.
//
//   pm2 start ecosystem.config.js                 # 전부 (서버 2 + RTSP 송출 21)
//   pm2 start ecosystem.config.js --only macs-system,evac-editor   # 서버만 (리허설 파일 모드는 이것으로 충분)
//   pm2 save                                      # 재부팅 후 복원(pm2 startup 과 함께)
//
// 사전조건
//   - conda env `boosttrack` (requirements.lock.txt) — CONDA_ENV 로 이름 변경 가능
//   - RTSP 송출을 쓸 때만: mediamtx(:8554) + 영상 파일
//       bash tools/rtsp/setup_rtsp_streams.sh --all   → ~/rtsp-stream/*.mp4 (+ mediamtx 컨테이너)
//       bash tools/fetch_assets.sh --field            → field/encoded/**
//     영상이 없는 송출은 목록에서 자동으로 빠진다.
//
// 환경변수(기본값은 운영 서버 기준)
//   INGEST_BACKEND=deepstream | ffmpeg   GPU_DEVICES=1   (GPU 1장 서버면 0)
//   MACS_PORT=8900  EVAC_PORT=8910  STREAM_DIR=~/rtsp-stream  CONDA_ENV=boosttrack
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = __dirname;
const ENV = process.env;
const CONDA_ENV = ENV.CONDA_ENV || 'boosttrack';
const STREAM_DIR = ENV.STREAM_DIR || path.join(os.homedir(), 'rtsp-stream');
const RELAY = path.join(ROOT, 'tools/rtsp/field_relay.sh');   // <mp4> <rtsp 경로명> — 재인코딩 없이 무한 루프 송출

const relay = (name, file) => ({
  name, script: RELAY, args: [file, name], interpreter: 'bash', cwd: ROOT, autorestart: true,
});

// 공개 테스트 영상 12개 (HF backseollgi/MCMOT/videos) — 1_v1·2_v1·3_v1 이 17F cam01~03
const TEST_STREAMS = ['1_v1', '2_v1', '3_v1', 'sample1', 'zara01', 'zara02', 'eth', 'hotel',
  'students03', 'arxiepiskopi', 'in_out_counting', 'inout_sample2'];
// 현장 훈련영상 9개 — 16F 6대(cam04~09) + 지상1층 3대(cam10~12)
const fieldFiles = ['1F', '16F'].flatMap((d) => {
  const dir = path.join(ROOT, 'field/encoded', d);
  return fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => f.endsWith('.mp4')).sort().map((f) => path.join(dir, f)) : [];
});

module.exports = {
  apps: [
    {
      name: 'macs-system',                       // 멀티카메라 시스템 :8900
      script: path.join(ROOT, 'tools/run_system_server.sh'),
      interpreter: 'bash', cwd: ROOT,
      env: {
        INGEST_BACKEND: ENV.INGEST_BACKEND || 'deepstream',
        GPU_DEVICES: ENV.GPU_DEVICES || '1',
        MACS_PORT: ENV.MACS_PORT || '8900',
        CONDA_ENV,
      },
    },
    {
      name: 'evac-editor',                       // CAD 도면 편집기 :8910
      script: 'conda', interpreter: 'none',
      args: ['run', '--no-capture-output', '-n', CONDA_ENV, 'python', '-m', 'uvicorn', 'evac.web:app',
        '--host', '0.0.0.0', '--port', ENV.EVAC_PORT || '8910'],
      cwd: path.join(ROOT, 'cad/CAD_API_Based_Optimal_Evacuation_Route_Algorithm'),
    },
    ...TEST_STREAMS.map((n) => [n, path.join(STREAM_DIR, `${n}.mp4`)])
      .filter(([, f]) => fs.existsSync(f)).map(([n, f]) => relay(n, f)),
    ...fieldFiles.map((f) => relay(path.basename(f, '.mp4'), f)),
  ],
};
