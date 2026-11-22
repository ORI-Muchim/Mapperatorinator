# 핸드오프: EZ2AC 5K 채보로 Mapperatorinator 학습하기 (내 PC)

이 문서는 클라우드 세션에서 준비한 작업을 **GPU가 있는 내 PC에서 이어서 학습**하기 위한 안내입니다. 위에서부터 순서대로 따라 하면 됩니다. PC의 Claude Code에게 맡길 때는 [6장](#6-pc의-claude-code에게-맡길-때)의 프롬프트를 쓰세요.

- 레포: `https://github.com/ORI-Muchim/Mapperatorinator`, 브랜치 `ez2ac-5k` (PR [ORI-Muchim/Mapperatorinator#1](https://github.com/ORI-Muchim/Mapperatorinator/pull/1))
- 목표: V32 모델의 osu!mania 체크포인트(`gamemode=3`)를 EZ2AC 5K 채보로 LoRA 파인튜닝

## 0. 체크리스트

- [ ] 1. 레포 받기 (`ez2ac-5k` 브랜치)
- [ ] 2. 학습 환경 준비 (Docker + WSL2 권장)
- [ ] 3. MR 음원(OGG) 433곡 + 매니페스트 CSV 준비
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

**아직 못 한 것**

- 실제 MR 음원으로 데이터셋 만들기
- GPU 학습과 추론, Docker 이미지 빌드
- MR이 없는 24곡 처리 (키음만으로 된 곡. 일단 제외 권장)

## 2. 준비물

| 항목 | 내용 |
|---|---|
| GPU | NVIDIA GPU + 최신 드라이버. bf16을 쓰려면 RTX 30 시리즈 이상 |
| 디스크 | 여유 공간 20GB 이상 (Docker 이미지, 데이터셋, 체크포인트) |
| 키 | `KSongEZ.cpp` (`g_ezKeyTable`이 들어 있는 소스). 경로를 그대로 `--key`에 지정 |
| 채보 | EZ2AC 5K `.ez` (곡 폴더별: `10Anti/5keymix1p-10anti-hd.ez` …) |
| MR 음원 | 곡별 MR OGG (EZ2AC_RE 도구로 `.ssf`에서 추출) |
| 인터넷 | 처음 실행할 때 Hugging Face에서 `OliBomby/Mapperatorinator-v32` 다운로드 |

추천 폴더 배치 (Docker가 레포의 형제 폴더 `datasets`를 `/workspace/datasets`로 연결합니다):

```
작업폴더/
  Mapperatorinator/            ← 이 레포 (ez2ac-5k 브랜치)
    tools/ez2ac/keys/          ← 키 파일을 여기 두면 git에서 자동 제외 (선택)
  datasets/
    ez2ac_src/charts/<곡>/*.ez ← 원본 채보
    ez2ac_src/mr/<곡>.ogg      ← MR 음원
    ez2ac_src/mr_manifest.csv
    ez2ac_5k/                  ← build_dataset.py 출력 (학습 데이터셋)
```

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

**방법 B — Windows에서 venv.** Python 3.10–3.12, ffmpeg, [PyTorch 2.10 (CUDA)](https://pytorch.org/get-started/locally/)을 설치한 뒤 의존성을 설치합니다.

```bash
pip install -r requirements.txt
```

이 경우 Flash Attention과 `torch.compile`이 동작하지 않을 수 있습니다. 학습 명령에 `attn_implementation=sdpa compile=false`를 붙이세요.

### 3.3 MR 음원과 매니페스트

- 433곡의 MR을 OGG로 추출합니다. 원본 샘플레이트를 유지하고 품질은 q5 정도면 충분합니다. 학습할 때 16kHz 모노로 변환됩니다.
- 매니페스트 CSV 형식 (`audio`는 매니페스트 파일 기준 상대 경로도 됩니다):

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

`mr_sample`과 `mr_ms`가 둘 다 없으면 MR을 추정합니다(BGM 트랙에서 샘플 0번과 1번 중 먼저 울리는 쪽). 추정이 맞지 않는 경우가 있으니 매니페스트에 정확한 값을 넣는 것이 가장 안전합니다. 특히 [부록의 확인 필요 목록](#부록-mr-확인이-필요한-곡)에 있는 곡은 꼭 넣어 주세요.

MR이 없는 곡(같은 목록의 1번, 2번)은 처음에는 매니페스트에서 빼세요. 키음만으로 된 곡이라 MR을 따로 합성해야 합니다.

### 3.4 데이터셋 생성

컨테이너 경로 기준 예시입니다. Windows venv라면 경로만 바꾸세요.

```bash
python tools/ez2ac/build_dataset.py --key tools/ez2ac/keys/KSongEZ.cpp --ez-root /workspace/datasets/ez2ac_src/charts --manifest /workspace/datasets/ez2ac_src/mr_manifest.csv --out /workspace/datasets/ez2ac_5k
```

- 끝나면 `Wrote N charts from M songs`가 출력됩니다. **곡 수 M을 적어 두세요.** 학습 설정에 필요합니다.
- `/workspace/datasets/ez2ac_5k/conversion_report.csv`에서 아래를 확인합니다.
  - `mr_source`가 `guessed`인 채보 → 매니페스트에 `mr_sample`/`mr_ms`를 추가
  - `dropped_before_mr`가 큰 채보 → MR 시작 시각이 틀렸을 가능성이 큼
  - `included`가 `False`인 채보 → 노트가 50개 미만이라 제외됨

### 3.5 데이터셋 검증 (GPU 불필요, 1–2분)

학습을 길게 돌리기 전에 학습 데이터 로더로 실제 샘플이 만들어지는지 확인합니다. `M`은 곡 수, `T`는 학습에 쓸 곡 수입니다(예: M=409면 T=389).

```bash
python tools/ez2ac/check_dataset.py --dataset /workspace/datasets/ez2ac_5k --train-end T --test-end M
```

마지막 줄에 `OK: the dataset loads through the training pipeline.`이 나와야 합니다.

### 3.6 학습 설정 맞추기

`configs/train/lora_ez2ac_5k.yaml`에서 아래 값을 바꿉니다. 명령줄에서 덮어써도 됩니다(3.7 참고).

| 키 | 기본값 | 바꿀 값 |
|---|---|---|
| `data.train_dataset_path`, `data.test_dataset_path` | `/workspace/datasets/ez2ac_5k` | 데이터셋 **절대 경로** |
| `data.train_dataset_end`, `data.test_dataset_start` | 410 | `T` (학습 곡 수, 약 95%) |
| `data.test_dataset_end` | 433 | `M` (전체 곡 수) |
| `optim.grad_acc` | 4 | VRAM이 부족하면 8 → 16 → 32 |

- **경로는 반드시 절대 경로로 쓰세요.** 학습이 시작되면 작업 폴더가 `logs/<날짜>/<시각>/`으로 바뀌어서 상대 경로가 깨집니다.
- **배치 크기 주의:** `optim.batch_size`(32)는 최종 배치 크기이고, GPU에 한 번에 올라가는 양은 `batch_size ÷ grad_acc`입니다. `batch_size`는 `grad_acc`로 나누어떨어져야 합니다. 메모리를 줄일 때는 `batch_size`는 그대로 두고 `grad_acc`만 올리세요.
- **RTX 3090 (24GB) 기준:** 기본값 `grad_acc=4`(한 번에 8개)로 시작하세요. 3090은 bf16과 Flash Attention 2를 지원합니다. 처음 수십 step 동안 `nvidia-smi`로 VRAM을 확인하고, 부족하면 8로 올리세요. 여유가 많으면 2(한 번에 16개, 원본 V32 학습 설정과 같음)로 내려도 됩니다. Windows venv에서 `sdpa`로 돌리면 메모리를 더 쓰니 8부터 시작하는 편이 안전합니다. 이 값들은 GPU에서 직접 재 본 값이 아니라 추정입니다.
- `optim.total_steps: 5000`은 시작값입니다. 학습 로그의 test loss를 보고 줄이거나 늘리세요.

### 3.7 학습 실행

wandb로 로그를 남깁니다. 그래프를 보려면 먼저 `wandb login`을 하고, 계정이 없으면 offline 모드로 돌리세요. 로깅 방식을 tensorboard로 바꾸면 체크포인트 저장 단계에서 에러가 나니 wandb는 그대로 두세요.

```bash
wandb login
```
```bash
python osuT5/train.py -cn lora_ez2ac_5k data.train_dataset_end=T data.test_dataset_start=T data.test_dataset_end=M
```

- 계정 없이 돌리려면 위 명령에 `logging.mode=offline`을 붙입니다.
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
| 디스크가 빠르게 참 | 체크포인트당 약 1GB. offline 모드에서는 `~/.local/share/wandb/artifacts/staging`에 사본이 더 쌓이니 정리 |
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

## 부록: MR 확인이 필요한 곡

433곡(채보 1,114개) 전체를 채보만으로 분석한 결과입니다. 곡 이름은 zip 안의 곡 폴더 이름입니다.

1. **MR 후보가 없음 (19곡, 모든 난이도):** 10blitz, 10harpe, 10yourmind, 118thplanet, 11afterlife, 12engine, 14wealive, 15unforget, 7bleed, 7brilliant, 7kamui, 7luvmsg, 7thrust, 8200re, 8axis, 8onyx, 8resonance, 9Artemis, 9north
2. **일부 난이도에만 MR 후보가 없음 (5곡):** 8nachthexen, 9beatrice, 9future, 9wheel, Backformore
3. **난이도마다 추정한 MR 시작이 20ms 넘게 다름 (22곡, 채보별 `mr_sample`/`mr_ms` 필요):** 15px, 6refresh, 6theboyrm, 7bc, 7final, 7messier, 7vintage, 8escape, 8morgenglut, Anytime, Dieoxin, Exist, Seize, Sparrow, Substance, catchtheflow, envy, freedom, funny, stayrm, therhythm, ztarwarz

추정값의 차이가 20ms 이하인 4곡(214, 5hyper, Jam, Saythat)은 틱 반올림 수준이라 그대로 써도 됩니다.

## 부록: CPU 스모크 테스트 결과

클라우드(CPU, GPU 없음)에서 5곡짜리 테스트 데이터셋으로 이 문서의 명령을 그대로 짧게 돌려 봤습니다. 테스트 음원은 실제 MR이 아니라 440Hz 톤입니다.

| 단계 | 결과 |
|---|---|
| `check_dataset.py` | 학습 40개, 테스트 39개 샘플 생성. `OK` 출력 |
| `train.py -cn lora_ez2ac_5k` (2 step, `device=cpu`, `logging.mode=offline`) | V32 `gamemode=3` 로딩, LoRA 학습 파라미터 1,120만 개(4.9%), loss 0.815 → 0.564, 체크포인트와 `lora/` 저장, 종료 코드 0 |
| `inference.py ... lora_path=.../lora` (8초 구간) | LoRA 로딩, mania 5K `.osu` 출력, 종료 코드 0. 노트는 0개 (톤 음원이라 생성할 리듬이 없음. 품질은 실제 MR로 확인 필요) |

참고로 체크포인트 하나는 약 1GB입니다(모델, 옵티마이저 상태, LoRA). offline 모드에서는 wandb가 체크포인트마다 사본을 `~/.local/share/wandb/artifacts/staging`에 따로 둡니다. 스모크 테스트 2회 저장에 1.9GB가 쌓였으니, 학습이 끝나면 확인하고 정리하세요.
