# 핸드오프: EZ2AC 5K 채보로 Mapperatorinator 학습하기 (내 PC)

이 문서는 클라우드 세션에서 준비한 작업을 **GPU가 있는 내 PC에서 이어서 학습**하기 위한 안내입니다. 위에서부터 순서대로 따라 하면 됩니다. PC의 Claude Code에게 맡길 때는 [6장](#6-pc의-claude-code에게-맡길-때)의 프롬프트를 쓰세요.

- 레포: `https://github.com/ORI-Muchim/Mapperatorinator`, 브랜치 `ez2ac-5k` (PR [ORI-Muchim/Mapperatorinator#1](https://github.com/ORI-Muchim/Mapperatorinator/pull/1))
- 목표: V32 모델의 osu!mania 체크포인트(`gamemode=3`)를 EZ2AC 5K 채보로 LoRA 파인튜닝

## 0. 체크리스트

- [ ] 1. 레포 받기 (`ez2ac-5k` 브랜치)
- [ ] 2. 학습 환경 준비 (Docker + WSL2 권장, 없으면 Windows venv)
- [ ] 3. MR 음원(OGG) 추출 + 매니페스트 CSV (`extract_mr.py`)
- [ ] 4. 데이터셋 생성 → `conversion_report.csv` 확인
- [ ] 5. `check_dataset.py`로 데이터셋 검증
- [ ] 6. 학습 설정(경로, 곡 수, 배치) 맞추기
- [ ] 7. 학습 실행
- [ ] 8. LoRA로 채보 생성해서 결과 확인

## 1. 현재 상태

**완료하고 검증한 것** (클라우드, CPU)

| 항목 | 결과 |
|---|---|
| `.ez` 복호화·파싱 | 채보 1,114개 전부 성공 (`EZFF` 헤더, 파싱 끝 위치 = 파일 크기) |
| `.osu` 변환 | 1,114개 전부 `slider` 파서로 읽힘 (노트 796,683개, 그중 롱노트 41,332개) |
| 학습 데이터 로딩 | 테스트용 5곡 데이터셋이 학습 데이터 로더와 V32 mania 토크나이저로 정상적으로 읽힘 |
| 학습·생성 | CPU에서 학습 2 step과 LoRA를 적용한 생성 스모크 테스트 통과 ([부록](#부록-cpu-스모크-테스트-결과)) |
| Dockerfile | `transformers` 4.57.3과 `rich` 등을 `requirements.txt` 버전에 맞춤 |

**PC에서 진행하고 검증한 것** (2026-11-22, Windows 11, RTX 3090 24GB, Windows venv)

| 항목 | 결과 |
|---|---|
| 키 | 실제 `KSongEZ.cpp`의 키 값은 `int`(최대 897)라 예전 `load_key`가 거부함 → 게임처럼 하위 8비트만 쓰도록 수정. `EZ2AC_RE/tools/gamedata.py`와 복호화 결과 동일 |
| MR 추출 | `extract_mr.py`로 `.ezi`+`.ssf`에서 채보별 MR 결정: 채보 1,081/1,114개, 422/433곡, MR 파일 450개 |
| 데이터셋 | 채보 1,081개, 422곡, 450 beatmap set, 노트 788,733개(롱노트 39,767개). 테스트 세트 20곡 |
| 타이밍 | 채보 120개 표본의 98%에서 노트 시각 ±20ms 구간의 온셋 강도가 ±300ms 오프셋 중 상위 10% (노트를 60ms 민 대조군은 2%) |
| `check_dataset.py` | `OK` (train/test 각 40샘플) |
| GPU 학습 스모크 | 40 step: 3.2초/step, 최대 VRAM 18.5GB(`grad_acc=4`, sdpa), train loss 0.40→0.33, test loss 0.392→0.384, 체크포인트·`lora/` 저장 |
| LoRA 생성 | 테스트 곡 10Anti(학습에 안 쓴 곡)에서 5K `.osu` 생성, 노트 1,372개(롱노트 0개), 온셋 정렬은 실제 채보와 같은 수준. 40 step이라 품질 판단은 아직 못 함 |

**아직 못 한 것**

- 본 학습(5,000 step, 약 5시간)과 결과 품질 확인
- MR이 없는 11곡(33채보) 처리. 루프·스템을 합성해야 함([부록](#부록-mr이-없어-빠진-채보))
- Docker 이미지 빌드 (이 PC에는 Docker·WSL이 없음)

## 2. 준비물

| 항목 | 내용 |
|---|---|
| GPU | NVIDIA GPU + 최신 드라이버. bf16을 쓰려면 RTX 30 시리즈 이상 |
| 디스크 | 여유 공간 20GB 이상 (Docker 이미지, 데이터셋, 체크포인트) |
| 키 | `KSongEZ.cpp` (`g_ezKeyTable`이 들어 있는 소스). 경로를 그대로 `--key`에 지정 |
| 채보 | EZ2AC 5K `.ez` (곡 폴더별: `10Anti/5keymix1p-10anti-hd.ez` …) |
| MR 음원 | 게임 폴더의 `.ssf`에서 `extract_mr.py`로 추출 (3.3) |
| 인터넷 | 처음 실행할 때 Hugging Face에서 `OliBomby/Mapperatorinator-v32` 다운로드 |

추천 폴더 배치 (Docker가 레포의 형제 폴더 `datasets`를 `/workspace/datasets`로 연결합니다):

```
작업폴더/
  Mapperatorinator/            ← 이 레포 (ez2ac-5k 브랜치)
    tools/ez2ac/keys/          ← 키 파일을 여기 두면 git에서 자동 제외 (선택)
  datasets/
    ez2ac_src/mr/<곡>/<샘플>.ogg ← extract_mr.py 출력: MR 음원
    ez2ac_src/mr_manifest.csv    ← extract_mr.py 출력: 채보별 MR
    ez2ac_src/mr_report.csv
    ez2ac_5k/                    ← build_dataset.py 출력 (학습 데이터셋)
```

채보는 게임 폴더(`<게임>/Sound/<곡>/`)에서 바로 읽습니다. 이 PC에서는 `H:\EZ2AC\datasets\`를 썼습니다.

## 3. 단계별 진행

### 3.1 레포 받기

```bash
git clone -b ez2ac-5k https://github.com/ORI-Muchim/Mapperatorinator
```

### 3.2 학습 환경

**방법 A — Docker + WSL2 (권장).** Flash Attention과 `torch.compile`이 그대로 동작합니다. Docker Desktop(WSL2 백엔드)과 NVIDIA 드라이버를 설치합니다. 처음 빌드할 때 Flash Attention을 컴파일하느라 오래 걸립니다(수십 분 이상). 아래 명령은 레포 폴더에서 실행합니다.

```bash
docker compose up -d --force-recreate
```
```bash
docker attach mapperatorinator_space
```
```bash
cd /workspace/Mapperatorinator
```

이후 명령은 모두 컨테이너 안에서 실행합니다. 경로는 `/workspace/Mapperatorinator`(레포)와 `/workspace/datasets/...`(데이터)입니다.

**방법 B — Windows에서 venv.** Python 3.10–3.12, ffmpeg(PATH에 있어야 함), [PyTorch 2.10 (CUDA)](https://pytorch.org/get-started/locally/)을 설치한 뒤 의존성을 설치합니다. 2026-11-22에 이 PC에서 쓴 명령입니다(`uv` 사용, `.venv`는 git 제외).

```bash
uv venv .venv --python 3.11
```
```bash
uv pip install --python .venv/Scripts/python.exe torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu130
```
```bash
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

이후 `python`은 `.venv/Scripts/python.exe`를 뜻합니다. Flash Attention과 `torch.compile`(triton)은 Windows에서 동작하지 않으니 학습 명령에 `attn_implementation=sdpa compile=false`를 붙이세요. 생성(`inference.py`)은 기본값 `attn_implementation=auto`가 알아서 sdpa를 고릅니다.

### 3.3 MR 음원과 매니페스트

게임 폴더(`.ez`, `.ezi`, `.ssf`)가 있으면 `extract_mr.py`가 MR 추출과 매니페스트 작성을 한 번에 합니다(Windows venv 예시, 약 2분).

```bash
python tools/ez2ac/extract_mr.py --key H:/EZ2AC/EZ2AC_RE/src/KSongEZ.cpp --sound "H:/EZ2AC/EZ2AC FINAL EX Ver.1.50/EZ2AC FINAL EX/Sound" --out H:/EZ2AC/datasets/ez2ac_src
```

- 채보마다 `.ezi`(샘플 목록)를 복호화하고, 레인 밖 트랙이 재생하는 20초 이상 샘플 중 가장 긴 것을 MR로 고릅니다. 슬롯 번호를 추정하지 않고 실제 파일 길이로 고르므로 MR이 2번 이상 슬롯에 있는 곡도 맞게 찾습니다.
- 음악이 루프나 파트별 스템으로 쪼개진 곡은 MR이 없다고 보고 뺍니다(부록 참고).
- 결과: `mr/<곡>/<샘플>.ogg`(libvorbis q5, 원본 샘플레이트. 학습할 때 16kHz 모노로 변환), 채보 단위 `mr_manifest.csv`, 선택 근거와 뺀 이유가 있는 `mr_report.csv`. 이미 있는 OGG는 건너뛰니 중단돼도 다시 실행하면 됩니다.
- 2026-11-22 실행 결과: 채보 1,114개 중 1,081개(433곡 중 422곡), MR 파일 450개(955MB). OGG 길이는 `.ssf` 헤더와 450개 모두 일치.

매니페스트를 손으로 쓸 때의 형식 (`audio`는 매니페스트 파일 기준 상대 경로도 됩니다):

```csv
song,audio,chart,mr_sample,mr_ms
10Anti,mr/10Anti.ogg,5keymix1p-10anti.ez,0,
10Anti,mr/10Anti.ogg,5keymix1p-10anti-hd.ez,0,
o2cross,mr/o2cross.ogg,,,3000
```

| 열 | 설명 |
|---|---|
| `song` | 채보가 들어 있는 곡 폴더 이름 (필수) |
| `audio` | MR 음원 경로 (필수). 음원이 없는 곡은 건너뜀 |
| `chart` | 채보 파일 이름. 비우면 그 곡의 모든 채보에 적용 |
| `mr_sample` | 그 채보의 `.ezi`에서 MR의 샘플 번호 (**채보 단위로 적기 권장**) |
| `mr_ms` | MR 시작 시각(ms). 적으면 `mr_sample`보다 우선 |

`mr_sample`과 `mr_ms`가 둘 다 없으면 MR을 추정합니다(BGM 트랙에서 샘플 0번과 1번 중 먼저 울리는 쪽). 이 추정은 MR이 2번 이상 슬롯에 있으면 틀리므로, 게임 폴더가 있으면 `extract_mr.py`의 매니페스트를 쓰세요. MR 시작을 찾지 못한 채보는 데이터셋에서 빠집니다.

### 3.4 데이터셋 생성

게임의 `Sound` 폴더를 그대로 `--ez-root`로 씁니다. 곡 폴더에 7K·10K 등 다른 모드 채보도 있으므로 `--chart-glob`으로 5K만 고릅니다(약 15초).

```bash
python tools/ez2ac/build_dataset.py --key H:/EZ2AC/EZ2AC_RE/src/KSongEZ.cpp --ez-root "H:/EZ2AC/EZ2AC FINAL EX Ver.1.50/EZ2AC FINAL EX/Sound" --chart-glob "5keymix1p*.ez" --manifest H:/EZ2AC/datasets/ez2ac_src/mr_manifest.csv --out H:/EZ2AC/datasets/ez2ac_5k
```

- 끝나면 `Wrote N charts from M songs (S beatmap sets)`와 권장 분할이 출력됩니다. **학습 설정의 분할은 곡 수가 아니라 beatmap set 수 S 기준입니다.** 한 곡 안에서 난이도마다 MR 파일이 다르면(radio·club 편곡 등, 26곡) 데이터 로더가 세트당 음원 하나만 읽기 때문에 `data/<곡>@<음원 이름>/` 세트로 나뉩니다. 나뉜 세트는 ID가 이어져 있어서 학습/테스트로 갈라지지 않습니다.
- 2026-11-22 결과: 채보 1,081개, 422곡, **450세트**, 노트 788,733개(롱노트 39,767개). 모두 매니페스트의 `mr_sample`을 씀(`guessed` 0개).
- `conversion_report.csv`에서 아래를 확인합니다.
  - `mr_source`가 `guessed`인 채보 → 매니페스트에 `mr_sample`/`mr_ms`를 추가
  - `dropped_before_mr`가 큰 채보 → MR 시작 시각이 틀렸을 가능성이 큼. 지금은 키음 인트로가 12–15초 있는 5곡(6feedback, 71234re, 7dance, 7final, Give2me)만 채보당 6–105개이고, 인트로 소리는 MR에 없으므로 빼는 것이 맞습니다.
  - `included`가 `False`인 채보 → `warnings`에 이유(음원 없음, MR 시작 없음, 노트 50개 미만)

### 3.5 데이터셋 검증 (GPU 불필요, 1–2분)

학습을 길게 돌리기 전에 학습 데이터 로더로 실제 샘플이 만들어지는지 확인합니다. `M`은 beatmap set 수, `T`는 학습에 쓸 세트 수입니다(지금 데이터셋은 M=450, T=428).

```bash
python tools/ez2ac/check_dataset.py --dataset H:/EZ2AC/datasets/ez2ac_5k --train-end 428 --test-end 450
```

마지막 줄에 `OK: the dataset loads through the training pipeline.`이 나와야 합니다. 2026-11-22 결과: train 40샘플(노트 3,616개), test 40샘플(노트 5,703개), `OK`.

### 3.6 학습 설정 맞추기

`configs/train/lora_ez2ac_5k.yaml`에서 아래 값을 바꿉니다. 명령줄에서 덮어써도 됩니다(3.7 참고).

| 키 | 기본값 | 바꿀 값 |
|---|---|---|
| `data.train_dataset_path`, `data.test_dataset_path` | `/workspace/datasets/ez2ac_5k` | 데이터셋 **절대 경로** |
| `data.train_dataset_end`, `data.test_dataset_start` | 428 | `T` (학습 세트 수, 약 95%) |
| `data.test_dataset_end` | 450 | `M` (전체 beatmap set 수) |
| `optim.grad_acc` | 4 | VRAM이 부족하면 8 → 16 → 32 |

- **경로는 반드시 절대 경로로 쓰세요.** 학습이 시작되면 작업 폴더가 `logs/<날짜>/<시각>/`으로 바뀌어서 상대 경로가 깨집니다.
- **배치 크기 주의:** `optim.batch_size`(32)는 최종 배치 크기이고, GPU에 한 번에 올라가는 양은 `batch_size ÷ grad_acc`입니다. `batch_size`는 `grad_acc`로 나누어떨어져야 합니다. 메모리를 줄일 때는 `batch_size`는 그대로 두고 `grad_acc`만 올리세요.
- **RTX 3090 (24GB) 실측 (Windows venv, sdpa, compile 없음):** 기본값 `grad_acc=4`(한 번에 8개)에서 최대 VRAM 18.5GB(데스크톱 사용분 1.5GB 포함), 3.2초/step. 그대로 쓰면 됩니다. 2로 내리면 한 번에 16개라 24GB를 넘을 가능성이 큽니다. Docker에서 Flash Attention 2를 쓰면 메모리와 시간이 더 줄 수 있습니다(미측정).
- `optim.total_steps: 5000`은 시작값입니다. 3.2초/step이면 학습만 약 4.5시간, 평가(500 step마다 100 step)를 포함하면 약 5시간입니다. 학습 로그의 test loss를 보고 줄이거나 늘리세요.

### 3.7 학습 실행

wandb로 로그를 남깁니다. 그래프를 보려면 먼저 `wandb login`을 하고, 계정이 없으면 offline 모드로 돌리세요. 로깅 방식을 tensorboard로 바꾸면 체크포인트 저장 단계에서 에러가 나니 wandb는 그대로 두세요.

```bash
wandb login
```
```bash
python osuT5/train.py -cn lora_ez2ac_5k data.train_dataset_end=T data.test_dataset_start=T data.test_dataset_end=M
```

Windows venv에서 이 PC의 데이터셋으로 돌릴 때(분할은 설정 기본값 428/450):

```bash
.venv/Scripts/python.exe osuT5/train.py -cn lora_ez2ac_5k data.train_dataset_path=H:/EZ2AC/datasets/ez2ac_5k data.test_dataset_path=H:/EZ2AC/datasets/ez2ac_5k attn_implementation=sdpa compile=false logging.mode=offline
```

- 계정 없이 돌리려면 `logging.mode=offline`을 붙입니다(위 Windows 명령에는 들어 있음).
- Windows venv라면 `attn_implementation=sdpa compile=false`도 붙입니다.
- 메모리가 부족하면 `optim.grad_acc=8`처럼 올립니다.
- 처음 실행하면 Hugging Face에서 V32 mania 체크포인트를 받습니다.
- 로그와 체크포인트는 `logs/<날짜>/<시각>/`에 쌓입니다. LoRA 가중치는 `logs/<날짜>/<시각>/checkpoints/checkpoint_<N>/lora/`에 저장됩니다. 기본 설정은 1,000 step마다 저장하고 최근 2개만 남깁니다.
- 중단된 학습을 이어서 하려면 `checkpoint_path=<체크포인트 폴더 절대 경로>`를 붙입니다.

### 3.8 결과 확인

학습한 LoRA로 채보를 생성합니다. `auto_select_gamemode_model`이 켜져 있어서 `gamemode=3`이면 mania 체크포인트를 고르고, LoRA는 `gamemode=3`에서만 적용됩니다.

```bash
python inference.py audio_path=/abs/path/song.ogg output_path=/abs/path/out gamemode=3 keycount=5 difficulty=3.0 year=2024 lora_path=/abs/path/logs/<날짜>/<시각>/checkpoints/checkpoint_<N>/lora
```

- `year`는 데이터셋을 만들 때 기록한 값(기본 2024)과 같게 줍니다.
- `difficulty`는 osu!mania star rating 기준입니다. EZ2AC 난이도별 대략적인 범위는 `metadata.parquet`의 `DifficultyRating`을 참고하세요.
- 생성된 `.osu`는 osu!에서 열어보거나 MaiMod(`python mai_mod.py beatmap_path=...`)로 확인할 수 있습니다.
- 같은 곡을 LoRA 없이(`lora_path` 생략) 생성해서 비교하면 학습 효과를 보기 쉽습니다.

## 4. 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `AssertionError` (`batch_size % grad_acc`) | `optim.batch_size`가 `grad_acc`로 나누어떨어지지 않음. `batch_size=32`에 `grad_acc` 4/8/16/32 |
| `AssertionError` (`eval.every_steps % logging.every_steps`) | 평가 주기가 로깅 주기(기본 10)의 배수가 아님 |
| CUDA out of memory | `optim.grad_acc`를 2배씩 올리기 |
| `transformers>=4.57.3 is required` | 옛 Docker 이미지. `docker compose up -d --force-recreate --build`로 다시 빌드 |
| `No module named 'pyaudioop'` | Python 3.13 이상. `pip install audioop-lts` |
| Flash Attention 또는 `torch.compile` 관련 에러 | `attn_implementation=sdpa compile=false` |
| `Failed to load audio file` | ffmpeg 미설치 또는 매니페스트의 음원 경로 오류 |
| 데이터셋 경로를 못 찾음 | 상대 경로를 썼음. 절대 경로로 바꾸기 |
| wandb 로그인 요청에서 멈춤 | `wandb login` 또는 `logging.mode=offline` |
| 디스크가 빠르게 참 | 체크포인트당 약 1GB. offline 모드에서는 wandb가 체크포인트마다 사본을 따로 둠: Linux·Docker는 `~/.local/share/wandb/artifacts/staging`, Windows는 `%LOCALAPPDATA%\wandb\wandb\artifacts\staging`. 학습이 끝나면 정리 |
| `.ez` 키 로딩 에러 (`neither a 512-byte key file nor a source file`) | 예전 `ez_format.py`. 키 값이 255를 넘는 실제 `KSongEZ.cpp`는 이번 수정 이후 버전에서 읽힘 |
| 생성된 노트가 음악보다 일정하게 빠르거나 느림 | MR 오프셋이 틀림. `conversion_report.csv`의 `mr_ms` 확인 |

## 5. 하지 말 것

- 키(`KSongEZ.cpp`, `g_ezKeyTable`), `.ez`/`.ezi`/`.ezz`/`.ssf`, 추출한 음원, 생성된 데이터셋, 체크포인트를 **git에 커밋하거나 공개 레포에 올리지 마세요.** 키 폴더와 게임 파일 확장자는 `.gitignore`로 막혀 있지만, `datasets/`와 `logs/`는 레포 밖에 두는 것이 안전합니다.
- 채보와 음원의 사용 범위는 권리자에게 확인해 두세요. 학습한 모델과 생성된 채보도 마찬가지입니다.

## 6. PC의 Claude Code에게 맡길 때

레포 폴더에서 Claude Code를 열고, 경로를 실제 값으로 바꿔서 붙여 넣으세요.

```text
tools/ez2ac/HANDOFF.md를 읽고 3.4부터 순서대로 진행해줘.
- 키: H:\EZ2AC\EZ2AC_RE\src\KSongEZ.cpp
- 채보 폴더: <곡 폴더들이 있는 경로>
- MR 매니페스트: <mr_manifest.csv 경로>
- 학습 환경: <Docker / Windows venv>, GPU: <모델명과 VRAM>
각 단계가 끝나면 결과(곡 수, conversion_report 요약, check_dataset 출력, 학습 첫 100 step 로그)를 보여주고,
에러가 나면 4장 표부터 확인해줘. 키와 데이터는 절대 커밋하지 마.
```

## 부록: MR이 없어 빠진 채보

`extract_mr.py`가 게임 폴더의 `.ezi`와 `.ssf`로 고른 결과입니다(2026-11-22, `mr_report.csv`). 예전 클라우드 분석(채보만 보고 샘플 0/1번 추정)에서 "MR 후보 없음"이었던 19곡 중 대부분(10blitz, 10harpe, 10yourmind, 11afterlife 등)은 MR이 2번 슬롯에 있었고, 지금은 데이터셋에 들어 있습니다.

| 이유 | 곡 (채보 수) |
|---|---|
| 20초 이상 샘플이 없음 (키음만으로 된 곡) | 12lastknot (3), 7apollon (3), 7happiness (2), 7messier (2), 7mm (2), stayrm (HD 1: 샘플 154개의 `.ssf`가 폴더에 없음) |
| 가장 긴 샘플이 곡의 60% 미만 (루프로 쪼개짐) | 118thplanet (3), 11smile (2), 12engine (3), 7bc (3), 9future (2), 8morgenglut (HD·SHD 2, NM은 MR 있음), Backformore (SHD 1) |
| 비슷한 길이의 샘플이 여럿 (파트별 스템) | 7kamui (4: 베이스 110초 + 스트링 57초) |

이 11곡(일부 난이도만 빠진 곡 포함 33채보)은 MR을 합성해야 쓸 수 있습니다. 그 밖에 참고할 것:

- 15Mac(4채보)은 MR이 90초인데 노트가 133초까지, shout(2채보)는 17초 더 있습니다. 데이터 로더는 음원이 끝난 뒤의 노트를 학습 샘플에 넣지 않으므로 MR 구간만 학습됩니다.
- 8core는 MR을 두 번 트리거하지만, 두 번째(141.6초)는 MR과 마지막 노트가 끝난 뒤라 영향이 없습니다.

## 부록: CPU 스모크 테스트 결과

클라우드(CPU, GPU 없음)에서 5곡짜리 테스트 데이터셋으로 이 문서의 명령을 그대로 짧게 돌려 봤습니다. 테스트 음원은 실제 MR이 아니라 440Hz 톤입니다.

| 단계 | 결과 |
|---|---|
| `check_dataset.py` | 학습 40개, 테스트 39개 샘플 생성. `OK` 출력 |
| `train.py -cn lora_ez2ac_5k` (2 step, `device=cpu`, `logging.mode=offline`) | V32 `gamemode=3` 로딩, LoRA 학습 파라미터 1,120만 개(4.9%), loss 0.815 → 0.564, 체크포인트와 `lora/` 저장, 종료 코드 0 |
| `inference.py ... lora_path=.../lora` (8초 구간) | LoRA 로딩, mania 5K `.osu` 출력, 종료 코드 0. 노트는 0개 (톤 음원이라 생성할 리듬이 없음. 품질은 실제 MR로 확인 필요) |

참고로 체크포인트 하나는 약 1GB입니다(모델, 옵티마이저 상태, LoRA). offline 모드에서는 wandb가 체크포인트마다 사본을 `~/.local/share/wandb/artifacts/staging`에 따로 둡니다. 스모크 테스트 2회 저장에 1.9GB가 쌓였으니, 학습이 끝나면 확인하고 정리하세요.
