# EZ2AC 5K → Mapperatorinator

EZ2AC 5K `.ez` 채보를 osu!mania 5K `.osu`로 변환하고, Mapperatorinator 학습용 `mmrs` 데이터셋을 만드는 도구입니다.

> **키와 게임 데이터는 git에 넣지 마세요.** `g_ezKeyTable`, `.ez`, `.ezi`, `.ssf`, 추출한 음원, 생성된 데이터셋은 `.gitignore`로 막혀 있습니다. 공개 레포에는 코드만 올립니다.

## 준비

- 키: `g_ezKeyTable` 512바이트. 바이너리 파일(512바이트) 또는 `g_ezKeyTable[512] = {...}`가 들어 있는 C/C++ 소스(`KSongEZ.cpp`)를 그대로 지정할 수 있습니다.
  소스의 값은 `int`라 255보다 큰 값이 있지만, 게임처럼 하위 8비트(`(char)`)만 씁니다. `extract_mr.py`는 같은 소스의 `g_eziKeyTable`도 읽습니다.
  `--key 경로`로 넘기거나 환경 변수 `EZ2AC_EZ_KEY`를 설정합니다. 레포 안에 둘 때는 `tools/ez2ac/keys/`(git 제외)에 둡니다.
- 의존성: `requirements.txt` (`rosu-pp-py`로 star rating 계산, `pandas`/`pyarrow`로 메타데이터 저장)

## 변환 규칙

| EZ2AC | osu!mania |
|---|---|
| 트랙 3–7 | 레인 1–5 (x = 51, 153, 256, 358, 460) |
| duration ≤ 6 | 일반 노트 (type 1) |
| duration > 6 | 롱노트 (type 128), 끝 = tick + duration |
| 트랙 0 type 3 / type 4 | 비상속 타이밍 포인트 (BPM / 박자) |
| 트랙 10–20 (턴테이블·페달·이펙트), 22–53 (BGM 키음) | 버림 |

- 1박 = 48틱 (해상도 192 = 한 마디). 0틱의 BPM은 헤더 값을 쓰고, 같은 틱의 BPM 이벤트가 여러 개면 마지막 값을 씁니다.
- 모든 시간에서 `mr_ms`(MR 샘플이 처음 울리는 시각)를 빼서 오디오 0ms = MR 시작이 되도록 맞춥니다. MR 시작 전 노트는 버립니다.
- 매니페스트에 `mr_sample` 또는 `mr_ms`를 적으면 그 값을 씁니다. `extract_mr.py`가 만든 매니페스트에는 채보마다 `mr_sample`이 들어 있습니다.
- 둘 다 없으면 MR 샘플을 추정합니다(BGM 트랙에서 샘플 0과 1 중 먼저 울리는 쪽). MR이 2번 이상 슬롯에 있는 곡이 있어서 추정은 틀릴 수 있습니다. MR 시작을 못 찾은 채보는 데이터셋에서 뺍니다.

## MR 추출과 매니페스트 (게임 폴더가 있을 때)

```bash
python tools/ez2ac/extract_mr.py --key KSongEZ.cpp --sound "<게임>/Sound" --out ../datasets/ez2ac_src
```

채보마다 `.ezi`(샘플 목록)와 `.ssf` 헤더로 MR을 고릅니다: 레인 밖 트랙이 재생하는 20초 이상 샘플 중 가장 긴 것. 그 샘플이 MR 시작부터 마지막 레인 노트까지의 60% 미만을 덮거나(음악이 루프로 쪼개진 곡), 두 번째로 긴 샘플이 그 절반 이상이면(파트별 스템) 그 채보는 뺍니다. 결과:

- `mr/<곡>/<샘플>.ogg`: MR 음원 (libvorbis q5, 원본 샘플레이트)
- `mr_manifest.csv`: 채보 단위 `song,audio,chart,mr_sample,mr_ms`
- `mr_report.csv`: 모든 채보의 선택 결과(샘플, 길이, coverage)와 뺀 이유

## 채보 하나 변환

```bash
python tools/ez2ac/ez2osu.py --key keys/g_ezKeyTable.bin "10Anti/5keymix1p-10anti-hd.ez" -o anti-hd.osu --audio audio.ogg
```

## 학습 데이터셋 만들기

매니페스트 CSV (곡 단위 행, 필요하면 `chart` 열로 채보 단위 덮어쓰기):

```csv
song,audio,chart,mr_sample,mr_ms
10Anti,mr/10Anti.ogg,,0,
o2cross,mr/o2cross.ogg,,,3000
15px,mr/15px.ogg,5keymix1p-15px.ez,1,
```

- `song`: `.ez`가 들어 있는 곡 폴더 이름
- `audio`: MR 음원 경로 (매니페스트 기준 상대 경로 가능). 음원이 없는 채보는 건너뜁니다.
- 한 곡 안에서 채보마다 MR 파일이 다르면(radio·club 편곡 등) 데이터 로더가 세트당 음원 하나만 읽기 때문에 세트를 나눕니다: 첫 MR은 `data/<곡>/`, 나머지는 `data/<곡>@<음원 이름>/`.

```bash
python tools/ez2ac/build_dataset.py --key keys/g_ezKeyTable.bin --ez-root EZ2AC_5K --manifest mr_manifest.csv --out ../datasets/ez2ac_5k
```

게임의 `Sound` 폴더를 그대로 쓸 때는 5K 채보만 고르도록 `--ez-root "<게임>/Sound" --chart-glob "5keymix1p*.ez"`를 줍니다(곡 폴더에 7K·10K 등 다른 모드 채보도 있음).

결과:

```
ez2ac_5k/
  metadata.parquet          # mmrs_dataset.py가 읽는 채보 목록 (StarRating: rosu-pp, 배속 0.5–2.0)
  conversion_report.csv     # 채보별 mr_ms, MR 추정 여부, 버린 노트 수, 경고
  data/<곡>/audio.ogg
  data/<곡>/<채보>.osu
```

`conversion_report.csv`에서 `mr_source=guessed`나 `dropped_before_mr`가 큰 채보는 매니페스트에 `mr_sample`/`mr_ms`를 넣어 바로잡으세요. `included=False`인 채보는 `warnings`에 뺀 이유가 있습니다.

학습 전에 데이터셋이 학습 데이터 로더로 제대로 읽히는지 확인합니다(GPU 불필요). `--train-end`와 `--test-end`는 학습/테스트 범위이고, 곡이 아니라 **beatmap set 수** 기준입니다(`build_dataset.py`가 마지막 줄에 출력).

```bash
python tools/ez2ac/check_dataset.py --dataset /abs/path/datasets/ez2ac_5k --train-end 410 --test-end 433
```

## 학습 (LoRA, V32 mania 체크포인트 기반)

내 PC에서 처음부터 끝까지 진행하는 순서는 [HANDOFF.md](HANDOFF.md)에 정리되어 있습니다.

`configs/train/lora_ez2ac_5k.yaml`의 데이터 경로와 train/test 곡 범위(`build_dataset.py`가 출력하는 곡 수 기준)를 맞춘 뒤:

```bash
python osuT5/train.py -cn lora_ez2ac_5k
```

## 생성

```bash
python inference.py audio_path=song.ogg output_path=out gamemode=3 keycount=5 difficulty=3.0 year=2024 lora_path=<LoRA 체크포인트>
```

데이터셋의 `year`는 기본 2024로 기록되므로 생성할 때도 같은 값을 주는 것이 좋습니다 (`build_dataset.py --year`로 변경).
