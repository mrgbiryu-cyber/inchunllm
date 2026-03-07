# 운영 기동/재기동 가이드 (AIBizPlan)

목표: 프론트/백엔드가 개발모드 없이 운영 모드(`next start`, `uvicorn`)로 항상 기동되고, 장애 발생 시 자동 복구되도록 구성합니다.

## 1) 운영 스크립트 위치

- 백엔드 기동: `deploy/scripts/start_backend_prod.sh`
- 프론트 기동: `deploy/scripts/start_frontend_prod.sh`
- 전체 재기동: `deploy/scripts/restart_services_prod.sh`
- 전체 중지: `deploy/scripts/stop_services_prod.sh`
- 상태 확인: `deploy/scripts/status_services_prod.sh`

## 2) 기본 운영 실행 (개발모드 아님)

```bash
cd /root/inchenmml/deploy/scripts
chmod +x start_backend_prod.sh start_frontend_prod.sh stop_services_prod.sh restart_services_prod.sh status_services_prod.sh

# 전체 재시작
./restart_services_prod.sh

# 상태 확인
./status_services_prod.sh
```

기본 포트
- 백엔드: `8000`
- 프론트: `3000`

환경변수(필요시 override)
- 백엔드: `APP_HOST`, `APP_PORT`, `UVICORN_WORKERS`, `LOG_LEVEL`
- 프론트: `FRONTEND_HOST`, `FRONTEND_PORT`, `FRONTEND_REBUILD` (`1`면 실행 전 빌드 재실행)

## 3) 통합 실행 스크립트 (권장)

루트의 `scripts/run_fullstack.sh`는 운영/개발 모드를 선택해 동작합니다.

- 운영 모드(기본): `bash scripts/run_fullstack.sh` → `deploy/scripts/restart_services_prod.sh` 실행
- 개발 모드: `bash scripts/run_fullstack.sh dev`

중지
- 운영 모드: `bash scripts/stop_fullstack.sh`
- 개발 모드: `bash scripts/stop_fullstack.sh dev`

### 상태 점검

- 운영: `bash scripts/check_fullstack.sh`
- 개발: `bash scripts/check_fullstack.sh dev`

## 4) systemd + logrotate 적용 (권장)

### 설치 스크립트

```bash
sudo bash /root/inchenmml/deploy/scripts/install_ops_services.sh
```

### 설치 단계(수동)

```bash
sudo cp /root/inchenmml/deploy/systemd/aibizplan-backend.service /etc/systemd/system/
sudo cp /root/inchenmml/deploy/systemd/aibizplan-frontend.service /etc/systemd/system/
sudo cp /root/inchenmml/deploy/logrotate/aibizplan-apps /etc/logrotate.d/aibizplan-apps

sudo systemctl daemon-reload
sudo systemctl enable --now aibizplan-backend
sudo systemctl enable --now aibizplan-frontend
sudo systemctl status aibizplan-backend aibizplan-frontend
```

### 로그 회전(수동)

```bash
sudo logrotate -d /etc/logrotate.d/aibizplan-apps
sudo logrotate -f /etc/logrotate.d/aibizplan-apps
```

## 5) 운영 상태 체크

```bash
curl -s http://127.0.0.1:8000/health
curl -I http://127.0.0.1:3000
curl -I http://127.0.0.1:3000/chat
```

