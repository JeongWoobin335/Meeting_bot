# ZOOM_MEETING_BOT Cross-Platform Test Handoff

## 목적

- Windows와 macOS에서 같은 기본 흐름으로 테스트를 시작하게 한다.
- 초기에 꼭 알아야 하는 OS별 주의사항을 한 장에 모은다.
- 지금 시점에서 무엇이 검증되었고, 무엇이 남았는지 혼동 없이 전달한다.

## 공통 기본 흐름

세 OS 공통으로 사용자가 기대하는 흐름은 아래입니다.

1. 부트스트랩
2. quickstart
3. create-session
4. 필요 시 stop / start

즉, 사용자는 복잡한 저수준 명령보다 아래 흐름만 따라가면 됩니다.

## Windows 실행 순서

PowerShell에서 실행:

```powershell
.\scripts\bootstrap.ps1
.\scripts\zoom-meeting-bot.ps1 quickstart --preset launcher_dm --yes
.\scripts\zoom-meeting-bot.ps1 create-session "회의링크" --passcode "암호" --open
```

런처를 다시 내렸다가 올릴 때:

```powershell
.\scripts\zoom-meeting-bot.ps1 stop
.\scripts\zoom-meeting-bot.ps1 start
```

## macOS 실행 순서

Terminal에서 실행:

```bash
chmod +x ./scripts/*.sh
./scripts/bootstrap.sh
./scripts/zoom-meeting-bot.sh quickstart --preset launcher_dm --yes
./scripts/zoom-meeting-bot.sh create-session "회의링크" --passcode "암호" --open
```

런처를 다시 내렸다가 올릴 때:

```bash
./scripts/zoom-meeting-bot.sh stop
./scripts/zoom-meeting-bot.sh start
```

## macOS 주의사항

- 명령어 앞에 `sudo`를 직접 붙이지 말 것
- 일반 Terminal에서 그대로 실행할 것
- `bootstrap.sh`는 `brew`가 없으면 자동 설치를 시도함
- `quickstart`는 필요한 도구, 모델, `whisper-cpp`, `BlackHole 2ch` 준비를 시도함
- `BlackHole 2ch`가 처음 설치되면 재부팅 1회가 필요할 수 있음
- 재부팅 안내가 뜨면 재부팅 후 같은 `quickstart --preset launcher_dm --yes`를 한 번 더 실행
- macOS가 `Microphone`, `Screen Recording` 권한을 물으면 허용해야 함

## Windows 주의사항

- PowerShell 기준으로 `.\scripts\...` 형태를 사용
- bare `zoom-meeting-bot` 명령은 `.venv` 활성화 후에만 바로 동작함
- 현재 검증 기준으로는 Windows 경로가 가장 안정적임

## 지금 기준으로 검증된 것

- Windows에서 기본 흐름
  - `bootstrap -> quickstart -> create-session`
  - 실제 회의 참가 및 결과물 생성
  - Telegram DM 전달
  - final transcription 성공 사례 확인
- GitHub에 올리기 위해 `whisper.cpp` 대용량 모델은 repo에서 제외함
- 대신 `quickstart/setup`가 기본 `whisper.cpp` 모델을 자동으로 확보하도록 변경함
- Windows fresh clone 기준
  - 기본 모델 자동 다운로드 확인
  - `whisper-cli.exe + DLL` asset 준비 확인
  - `whisper-cli.exe` 실제 실행 확인
- macOS는 코드상 blocker 정리 완료
  - 실제 맥북 실기 테스트는 아직 필요

## 지금 기준으로 핵심 변경점

GitHub 업로드를 위해 기존 흐름에서 바뀐 핵심은 아래 두 줄입니다.

1. `whisper.cpp` 대용량 모델 파일을 repo에서 뺐다.
2. 그 모델을 `quickstart/setup`에서 자동으로 받아오게 바꿨다.

즉, 사용자의 표면 흐름을 바꾸기보다 저장소 구성과 setup 준비 방식을 바꾼 것입니다.

## 테스트 핵심 포인트

- clone 후 README 또는 이 문서대로 바로 시작 가능한지
- `quickstart` 중 설치/모델 준비 단계가 자연스럽게 이어지는지
- `create-session`으로 회의 진입 페이지가 정상 열리는지
- 회의 종료 후 PDF와 Telegram 전달이 정상인지
- macOS라면
  - `chmod +x ./scripts/*.sh` 이후 실행이 자연스러운지
  - `brew`, `BlackHole`, 권한 허용 흐름에서 막히는지
  - `quickstart`만으로 `whisper-cli` 경로가 실제로 확보되는지


## 마지막 메모

- 이 문서는 내부 테스트 전달용입니다.
- 지금 시점에서 남은 것은 주로 `macOS 실기 검증`입니다.
