# Implementation Plan + Checklist: Grid Benchmark

**Datum zahajeni:** 2026-06-04  
**Metodologie:** Spec Kit - Plan + Tasks + Analyze  
**Typ zmeny:** feature / research-to-implementation  
**Status:** Draft  
**Navazujici specifikace:** `20260604_grid-benchmark-specification.md`

---

## 1. Cil

Implementovat `Grid benchmark` pro `llama-orchestrator`: rizeny sweep parametru nad jednim nebo vice modely, s validaci rozsahu, bezpecnym serialnim spoustenim, perzistentni historii, artefakty a volitelnym vyuzitim Hugging Face/GGUF metadat pro defaulty a audit.

Hlavni rozhodnuti: nerozsirovat "vsechny parametry" jako nekontrolovany editor CLI argumentu. Misto toho zavest katalog parametru s kategoriemi:

- `request` - meni se v HTTP requestu bez restartu;
- `runtime_static` - vyzaduje novy/restartovany `llama-server`;
- `model_metadata` - read-only fakta o architekture, GGUF a HF zdroji, ktera ridi defaulty a eligibility;
- `model_runtime` - runtime volby zavisle na architekture modelu, napr. KV cache, MTP/speculative decoding, SWA a checkpointy;
- `metadata_only` - slouzi pro vysvetleni a defaulty;
- `blocked` - detekovany parametr existuje, ale neni bezpecky gridovat v GUI.

## 2. Scope

### In scope

- Novy grid benchmark datovy model a validace.
- GUI dialog pro zadani minimum/maximum/step nebo enumerovanych hodnot.
- Serialni runner s progress, stop request a konzistentnim zapisem vysledku.
- SQLite tabulky pro `benchmark_sweeps` a `benchmark_runs` nebo kompatibilni aditivni rozsireni.
- Markdown/CSV/JSON export vysledku.
- Runtime introspekce `llama-server --help` pro informacni katalog parametru.
- Aditivni rozsireni HF metadat pro `sha/revision`, file metadata, licence/gated/private stav podle dostupnosti API.
- Model-aware katalog pro MTP/speculative decoding, architekturu modelu, KV cache a pametove scenare odvozene z GGUF.
- Testy helperu, DB migrace, dialogove logiky bez nutnosti spoustet realny model.

### Out of scope

- Paralelni sweep.
- Automaticke AI hodnoceni kvality.
- Trvale prepisovani configu bez potvrzeni.
- Benchmarkovani parametru jako `--model`, `--host`, `--port`, tokeny, log path, download URL, LoRA, control vectors a `--override-kv` v prvni verzi.
- Editace architektury modelu; architekturni metadata jsou read-only fakta, ne laditelne hodnoty.
- Web UI nebo jina GUI technologie.

## 3. Vstupni kontext

### Relevantni soubory a sluzby

- `src/llama_orchestrator/benchmark.py` - `BenchmarkSettings`, `BenchmarkResult`, `quick_benchmark_instance()`, SQLite historie, artefakty.
- `src/llama_orchestrator/gui.py` - toolbar, detail bar, `Quick benchmark`, `Serial benchmark`, `Params` menu, background worker.
- `src/llama_orchestrator/config/schema.py` - Pydantic rozsahy a `ParameterMutabilityConfig`.
- `src/llama_orchestrator/engine/command.py` - canonical command builder a extra `args`.
- `src/llama_orchestrator/memory_fit.py` - parsovani runtime argumentu a GGUF memory fit heuristiky.
- `src/llama_orchestrator/hf_import.py` - Hugging Face import GGUF variant, token store, local path resolution.
- `src/llama_orchestrator/model_metadata.py` - aditivni metadata z lokalniho GGUF a HF source fields.
- `tests/test_benchmark.py`, `tests/test_gui.py`, `tests/test_hf_import.py`, `tests/test_model_metadata.py` - existujici testovaci kotvy.
- `instances/*/config.json` - ukazuji realne `args` pro `--ubatch-size`, `--cache-type-*`, `--flash-attn`, speculative/MTP.

### Externi zavislosti nebo aktualni omezeni

- Hugging Face Hub API podporuje `HfApi`, token, `model_info`, `files_metadata` a `expand` vlastnosti jako `siblings`, `gguf`, `cardData`, `sha`, `tags`.
- `llama-server` CLI parametry jsou zavisle na verzi buildu; lokalni `b9286` obsahuje napriklad `--ctx-size`, `--batch-size`, `--ubatch-size`, `--cache-type-k`, `--cache-type-v`, `--n-gpu-layers`, `--split-mode`, `--flash-attn`, `--fit`.
- Lokalni `b9286` obsahuje speculative/MTP skupinu parametru: `--spec-type none,draft-simple,draft-eagle3,draft-mtp,...`, `--spec-draft-n-max`, `--spec-draft-n-min`, `--spec-draft-p-split`, `--spec-draft-p-min`, draft KV typy, draft model a draft GPU offload.
- Lokalni `b9286` obsahuje dalsi KV/cache parametry relevantni pro model-aware tuning: `--kv-offload`, `--kv-unified`, `--cache-ram`, `--cache-idle-slots`, `--ctx-checkpoints`, `--swa-full`, `--cache-prompt`, `--cache-reuse`.
- Soucasna benchmark historie neuklada request parametry jako samostatne sloupce; jsou jen v Markdown artefaktu a settings souhrnu.

## 4. Clarify a predpoklady

### Potvrzene informace

- Soucasny quick benchmark umi `max_tokens`, `temperature`, `top_p`, `top_k`, `repeat_penalty`, `seed`, `endpoint`, `ignore_eos`.
- GUI uz ma background job lock, serial benchmark stop event a frontu vybranych modelu.
- Import z Hugging Face uz vola `model_info(..., files_metadata=True)` a umi pracovat s gated/private chybami pres token.
- `model_metadata.py` uz cte lokalni GGUF metadata a umi volitelne nacist licenci z HF model card.
- `memory_fit.py` uz umi nacist GGUF metadata jako architekturu, context length, block count, embedding length, attention heads, KV heads, head dimenze, tokenizer, chat template a expert metadata.
- `model_metadata.py` uz odvozuje KV cache memory model pro vice kontextu a cache typu; tato logika je vhodny zaklad pro defaulty a varovani v grid dialogu.

### Predpoklady

- [ASSUMPTION] Prvni implementace pouzije vychozi limit 100 kombinaci bez potvrzeni a hard limit 1000 kombinaci s explicitnim potvrzenim.
- [ASSUMPTION] Vysledky gridu budou ukladane do novych tabulek, aby se nerozbilo cteni stavajici `benchmarks`.
- [ASSUMPTION] Runtime-static kombinace budou spoustene serialne s jednim aktivnim docasnym runtime planem.
- [ASSUMPTION] Prvni GUI vysledky mohou byt jednodussi tabulka plus export, pokud plnohodnotny analyzator presahne scope.

### Otevrene body

- Potvrdit limit kombinaci a defaultni rozsahy pro konkretni lokalni hardware.
- Rozhodnout, zda runtime-static sweep smi restartovat vybranou instanci, nebo musi vzdy vytvorit docasnou kopii procesu.
- Rozhodnout, zda se ma do prvni verze zahrnout speculative decoding grid (`--spec-type`, `--spec-draft-n-max`) pro MTP modely.
- Rozhodnout, zda KV/cache a MTP maji byt jeden spolecny "Model tuning" tab, nebo oddelene taby `KV cache` a `Speculative/MTP`.

## 5. Architektura nebo tok reseni

```text
GUI selection
  -> Grid benchmark dialog
     -> parameter catalog + current config + model metadata
     -> architecture-derived eligibility and memory warnings
     -> validation + combination preview
  -> benchmark sweep runner
     -> for each instance
        -> for each combination
           -> request-only: call quick benchmark with derived request settings
           -> model-runtime: prepare temporary runtime args, restart/start, wait healthy, call quick benchmark
           -> record benchmark_runs row
           -> write per-run artifact
  -> summary/export
```

Klicovy princip: `quick_benchmark_instance()` zustane jednotkovy vykonavac jednoho request benchmarku. Grid vrstva bude zodpovedna za enumeraci kombinaci, restart semantiku, sweep metadata a agregaci vysledku.

## 6. Navrh implementace

### Workstream A - Parameter catalog

- Cile:
  - Zavest explicitni katalog benchmarkovatelnych parametru.
  - Oddelit request parametry od runtime-static parametru.
  - Vyu zit Pydantic schema, GGUF metadata a `llama-server --help` jako zdroje defaultu, eligibility pravidel a informacniho popisu.
- Zasahy:
  - `src/llama_orchestrator/benchmark.py` nebo novy `benchmark_grid.py` - dataclass/Pydantic modely `GridParameterSpec`, `GridParameterRange`, `GridPlan`, `GridCombination`.
  - `src/llama_orchestrator/engine/command.py` nebo novy helper - introspekce `llama-server --help` s cache podle binary id/version.
  - `tests/test_benchmark_grid.py` - enumerace kombinaci, validace limitu, typy parametru.
- Poznamky:
  - Pro numericke hodnoty podporovat `min/max/step`.
  - Pro enum/bool hodnoty podporovat seznam hodnot, protoze `step` nedava smysl pro `endpoint`, `ignore_eos`, `cache-type`, `flash-attn`.
  - Pro architekturu modelu podporovat read-only metadata rows a odvozena doporuceni, ne editaci hodnot.

### Workstream M - Model-aware tuning catalog

- Cile:
  - Pridat MTP/speculative, KV cache a architekturu jako prvotridni cast grid benchmarku.
  - Zabránit tomu, aby GUI nabizelo kombinace, ktere model nebo binary pravdepodobne nepodporuje.
- Zasahy:
  - `benchmark_grid.py` - `ModelCapabilityProfile`, `KvCacheTuningProfile`, `SpeculativeTuningProfile`.
  - `model_metadata.py` / `memory_fit.py` - helper pro prevod GGUF metadata na read-only model profile a KV memory warnings.
  - `engine/command.py` nebo novy helper - detekce dostupnosti speculative flags z `llama-server --help`.
  - `tests/test_benchmark_grid.py` - eligibility pro MTP, KV cache typy, OOM warning threshold.
- Parametry:
  - Read-only model metadata: `architecture`, `native_context_length`, `n_layers`, `n_embd`, `n_attention_heads`, `n_kv_heads`, `head_dim_k`, `head_dim_v`, `n_experts`, `n_experts_used`, `tokenizer_model`, `chat_template`.
  - KV/cache runtime grid: `model.context_size`, `--cache-type-k`, `--cache-type-v`, `--kv-offload`/`--no-kv-offload`, `--kv-unified`, `--cache-ram`, `--cache-idle-slots`, `--ctx-checkpoints`, `--checkpoint-every-n-tokens`, `--swa-full`, `--flash-attn`, `--ubatch-size`, `model.batch_size`, `server.parallel`.
  - MTP/speculative runtime grid: `--spec-type`, `--spec-draft-n-max`, `--spec-draft-n-min`, `--spec-draft-p-min`, `--spec-draft-p-split`, `--cache-type-k-draft`, `--cache-type-v-draft`, `--n-gpu-layers-draft`, `--model-draft`.
- Poznamky:
  - `--override-kv` zustava v prvni verzi blocked, protoze meni model metadata na nizke urovni a je prilis rizikovy pro obecny grid.
  - MTP defaulty odvozovat z `model_metadata.speculative_decoding.builtin_mtp`, tagu jako `mtp`, nazvu souboru a dostupnosti `--spec-type draft-mtp`.
  - KV defaulty odvozovat z GGUF memory modelu a posledniho benchmark memory samplingu.

### Workstream B - Storage and artifacts

- Cile:
  - Ulozit sweep a kazdy run auditovatelne.
  - Zachovat stavajici quick benchmark historii.
- Zasahy:
  - `benchmark.py` nebo `benchmark_grid.py` - `init_grid_benchmark_db()`, `record_grid_sweep()`, `record_grid_run()`, `latest_grid_results()`.
  - SQLite:
    - `benchmark_sweeps(id, sweep_id, created_at, instance_names_json, prompt_file, prompt_sha256, status, grid_spec_json, total_runs, completed_runs, stopped_at, error)`.
    - `benchmark_runs(id, sweep_id, run_id, instance_name, combination_index, parameters_json, quick_benchmark_id nullable, status, metrics_json, artifact_file, error, started_at, finished_at)`.
  - `logs/<instance>/benchmarks/grid/<sweep_id>/` - artefakty s parametry v nazvu nebo frontmatter.
- Poznamky:
  - V `benchmark_runs.parameters_json` ukladat presne hodnoty, ne jen hash.

### Workstream C - Request-only runner

- Cile:
  - Zprovoznit grid pro parametry bez restartu.
  - Omezit blast radius prvni verze.
- Zasahy:
  - `benchmark_grid.py` - `run_request_grid_for_instance(config, grid_plan, callbacks)`.
  - `benchmark.py` - pripadne rozsirit `BenchmarkResult` o `benchmark_id` nebo vratit id z `record_benchmark_result()`.
  - `tests/test_benchmark_grid.py` - stop behavior, failed run recording, parameter body mapping.
- Poznamky:
  - Startovat vybranou instanci stejne jako serial benchmark, pokud nebezi.
  - Stop request dokonci aktualni kombinaci a zastavi pred dalsi.

### Workstream D - Runtime/model-static runner

- Cile:
  - Bezpecne benchmarkovat parametry, ktere meni proces nebo model-runtime profil.
  - Neznecistit ulozene `config.json`.
- Zasahy:
  - `benchmark_grid.py` - `apply_runtime_combination(config, combination) -> InstanceConfig` bez zapisu na disk.
  - `engine/process.py` integrace jen pokud existujici start/restart API neumozni docasny config.
  - Port allocation pres existujici `suggest_port_for_instance()` nebo docasne izolovane porty.
  - Tests pro mapovani `model.context_size`, `model.batch_size`, `server.parallel`, `gpu.layers`, KV cache args a MTP/speculative args.
- Poznamky:
  - Pokud by docasny in-memory config vyzadoval velky zasah do engine, fazi D rozdelit do samostatneho navazujiciho feature requestu.

### Workstream E - GUI dialog and UX

- Cile:
  - Pridat ovladani bez blokovani GUI.
  - Udelat rizika viditelna pred spustenim.
- Zasahy:
  - `gui.py` - tlacitko/menu `Grid benchmark`, dialog `GridBenchmarkDialog`, progress stav, stop.
  - Dialog tabulka:
    - `Enabled`
    - `Parameter`
    - `Current/default`
    - `Minimum`
    - `Maximum`
    - `Step / values`
    - `Category`
    - `Restart required`
    - `Eligibility / warning`
    - `Estimated memory impact`
  - Activity log zpravy `[Grid benchmark]`.
  - Tests helperu pro formatovani summary a validaci vstupu; GUI smoke manualni.
- Poznamky:
  - Nepouzivat jedno `simpledialog` po druhem; grid potrebuje jeden modalni dialog s preview poctu kombinaci.
  - Doporucene UX: taby `Sampling`, `KV cache`, `Speculative/MTP`, `Model metadata`, `Advanced/blocked`.

### Workstream F - Hugging Face metadata enhancement

- Cile:
  - Zlepsit model metadata pro audit a defaulty, bez blokovani gridu.
  - Nerozbit existujici import.
- Zasahy:
  - `hf_import.py` - zachytit a predat `repo sha/revision`, `cardData`, `tags`, `private/gated` podle dostupnosti.
  - `model_metadata.py` - vyplnit `source.revision`, `source.commit_hash`, `artifact.etag`, pripadne license fields bez povinneho network volani pri kazdem benchmarku.
  - `config/schema.py` - jen aditivne, pokud stavajici `ModelMetadata*` pole nestaci.
  - `tests/test_hf_import.py`, `tests/test_model_metadata.py`.
- Poznamky:
  - HF metadata nejsou nutna pro samotny grid; implementovat po request-only gridu nebo paralelne, pokud testy zustanou male.

### Workstream G - Verification and documentation

- Cile:
  - Prokazat, ze quick/serial benchmark zustaly kompatibilni.
  - Zapsat rizika a manualni smoke vysledky.
- Zasahy:
  - README sekce benchmark workflow.
  - Implementacni report v `reports/implementation/infra-local/llama-orchestrator/2026/`.
  - Test commandy a manualni GUI checklist.

## 7. Task breakdown

| ID | Faze | Ukol | Typ zasahu | Zavislosti | Parallel | Done kdyz |
|----|------|------|------------|------------|----------|-----------|
| A1 | A | Navrhnout `GridParameterSpec` a typy hodnot | code/test | spec review | no | Testy pokryvaji numeric, enum, bool parametry |
| A2 | A | Implementovat enumeraci kombinaci s limity | code/test | A1 | no | Pocet kombinaci a limit jsou deterministicke |
| A3 | A | Pridat katalog request parametru | code/test | A1 | [P] | Katalog obsahuje quick benchmark parametry |
| A4 | A | Pridat katalog runtime-static parametru | code/test | A1 | [P] | Katalog rozlisuje restart-required |
| A5 | A | Pridat informacni introspekci `llama-server --help` | code/test | A1 | [P] | Cache je vazana na binary id/version |
| M1 | M | Vytvorit read-only model capability profile z GGUF/metadat | code/test | A1 | [P] | Profil obsahuje architekturu, head/layer a expert fields |
| M2 | M | Pridat KV/cache tuning katalog a memory warnings | code/test | M1, A4 | no | Dialog umi oznacit OOM/shared-RAM riziko |
| M3 | M | Pridat MTP/speculative tuning katalog a eligibility | code/test | A5, M1 | no | MTP se povoli jen pri podpore modelu/binary nebo override |
| M4 | M | Pridat mapovani MTP/KV parametru na runtime args | code/test | M2, M3 | no | Testy pokryvaji draft/KV args bez zapisu configu |
| B1 | B | Navrhnout SQLite grid schema | code/test | A1 | no | Migrace je aditivni |
| B2 | B | Implementovat sweep/run insert/update helpers | code/test | B1 | no | Failed i ok runy se ulozi konzistentne |
| B3 | B | Implementovat grid artifact path a writer | code/test | B2 | [P] | Artefakt obsahuje parametry kombinace |
| C1 | C | Implementovat request-only runner | code/test | A2, B2 | no | Runner vola jednotkovy benchmark pro kazdou kombinaci |
| C2 | C | Doplnit stop request a progress callbacks | code/test | C1 | no | Stop dokonci aktualni run a oznaci sweep stopped |
| C3 | C | Propojit quick benchmark result s grid runem | code/test | B2, C1 | no | Vysledky jsou dohledatelne podle `sweep_id` |
| D1 | D | Navrhnout temporary runtime config aplikaci | code/test | A4, M4 | no | Puvodni config zustava beze zmen |
| D2 | D | Implementovat mapovani runtime parametru na config/args | code/test | D1, M4 | no | Testy pro ctx, batch, parallel, layers, KV, MTP args |
| D3 | D | Implementovat restart/start strategii pro runtime grid | code/test | D2, C1 | no | Runtime run ceka na healthy pred benchmarkem |
| E1 | E | Pridat GUI akci `Grid benchmark` | code | A1 | no | Akce dostupna pro vyber/frontu |
| E2 | E | Implementovat dialog s tabulkou parametru | code/test | A3, A4 | no | Dialog validuje min/max/step a enum hodnoty |
| E3 | E | Pridat preview kombinaci a potvrzeni limitu | code/test | E2, A2 | no | Uzivatel vidi pocet runu pred startem |
| E4 | E | Napojit background runner, progress a stop | code/test | C2, E1 | no | GUI se neblokuje |
| E5 | E | Pridat zobrazeni/export vysledku | code/test | B2, B3 | no | CSV/JSON/Markdown lze otevrit z GUI |
| F1 | F | Rozsirit HF variant metadata bez zmeny token storage | code/test | - | [P] | Repo sha/file metadata se zachyti, kdyz jsou dostupne |
| F2 | F | Dopsat metadata do `ModelMetadata` aditivne | code/test | F1 | no | Stare configy validuji dal |
| F3 | F | Pouzit metadata pro defaultni rozsahy a report | code/test | F2, A1 | [P] | Grid artifact obsahuje HF source fields |
| G1 | G | Aktualizovat README benchmark sekci | docs | E4 | [P] | Popisuje quick, serial a grid rozdily |
| G2 | G | Vytvorit implementacni report | docs | E5 | no | Report obsahuje testy, rizika, zname limity |
| G3 | G | Spustit scoped testy a ruff | test | all code | no | Testy projdou nebo jsou duvody zdokumentovane |

## 8. Acceptance criteria coverage

- [ ] AC: dialog a akce v GUI - pokryto E1, E2.
- [ ] AC: sampling parametry - pokryto A3, C1, E2.
- [ ] AC: runtime-static parametry - pokryto A4, D1-D3.
- [ ] AC: model architecture metadata - pokryto M1, E2.
- [ ] AC: KV/cache tuning - pokryto M2, M4, D1-D3.
- [ ] AC: MTP/speculative tuning - pokryto M3, M4, D1-D3.
- [ ] AC: preview a limity kombinaci - pokryto A2, E3.
- [ ] AC: perzistence `sweep_id/run_id/parameters` - pokryto B1-B3, C3.
- [ ] AC: stop behavior - pokryto C2, E4.
- [ ] AC: zadne trvale config mutace bez potvrzeni - pokryto D1, D2.
- [ ] AC: vysledky a export - pokryto E5, G2.
- [ ] AC: HF source fields - pokryto F1-F3.
- [ ] AC: quick/serial kompatibilita - pokryto G3.

## 9. Verifikace

### Automaticke kontroly

- `uv run pytest tests/test_benchmark.py tests/test_gui.py tests/test_hf_import.py tests/test_model_metadata.py tests/test_benchmark_grid.py -v --no-cov`
- `uv run ruff check src\\llama_orchestrator\\benchmark.py src\\llama_orchestrator\\gui.py src\\llama_orchestrator\\hf_import.py src\\llama_orchestrator\\model_metadata.py tests\\test_benchmark_grid.py`
- DB migration test nad prazdnou DB i DB se stavajici `benchmarks` tabulkou.

### Manualni GUI smoke

- Spustit GUI a overit, ze `Quick benchmark` se chova stejne jako pred zmenou.
- Spustit request-only grid se 2 teplotami x 2 top-p hodnotami.
- Spustit KV cache grid s malou matici `cache-type-k/v` a `ctx-size`, overit memory warning a artifact metadata.
- Spustit MTP/speculative grid na MTP-tagged modelu s `--spec-type draft-mtp` a dvema hodnotami `--spec-draft-n-max`, pokud je lokalni model k dispozici.
- Overit stop uprostred sweepu.
- Overit export a otevreni artefaktu.
- Overit, ze runtime-static grid ukaze restart warning a neprepise `instances/*/config.json`.
- Overit gated HF model bez tokenu a s tokenem, pokud je k dispozici testovaci repo.

### Artefakty

- SQLite rows pro sweep/run.
- Markdown artefakty pod `logs/<instance>/benchmarks/grid/<sweep_id>/`.
- CSV/JSON export vysledku.
- Implementacni report.

## 10. Rizika a rollback

| Riziko | Dopad | Mitigace | Rollback |
|--------|-------|----------|----------|
| Grid vygeneruje prilis mnoho behu | high | Limit + preview + potvrzeni | Vypnout GUI akci, ponechat quick benchmark |
| Runtime-static sweep poskodi stav instance | high | Docasny config bez zapisu, serialni beh, health wait | Zastavit docasny proces, obnovit puvodni runtime start |
| SQLite migrace zpusobi regresi quick benchmarku | high | Nove tabulky misto upravy existujicich sloupcu | Ignorovat grid tabulky, quick historie zustava |
| HF API metadata nejsou dostupna | medium | Metadata jsou optional, fallback na GGUF/local config | Preskocit F workstream bez blokovani gridu |
| GUI thread se zablokuje | high | Background runner + queue callbacks | Vratit GUI wiring, ponechat backend helpers |
| Parametr z `llama-server --help` bude spatne interpretovan | medium | Help katalog informacni, grid povolen jen kuratorovane | Deaktivovat parametr v katalogu |
| Warm/cold start zkresli runtime benchmark | medium | Ukladat `cold_start`, start latency, warmup policy | Reportovat a filtrovat runtime-static vysledky oddelene |
| MTP povolene pro nekompatibilni model | high | Eligibility podle metadat + explicitni override | Oznacit run failed, vypnout MTP katalog pro model |
| KV/cache kombinace vyvola OOM nebo presun do shared RAM | high | Preflight memory estimate + hard warning | Zastavit sweep, ponechat posledni stabilni config |


## 11. Doporučene poradi implementace

1. Implementovat A + B + C + minimalni E pro request-only grid.
2. Implementovat M1/M2 pro read-only architekturu a KV cache doporuceni, protoze to zlepsi bezpecnost runtime gridu.
3. Overit UX a datovy model na realnem modelu.
4. Teprve potom pridat D runtime/model-static grid.
5. MTP/speculative M3/M4 pridat po KV cache gridu, nebo driv jen pro modely oznacene `mtp`.
6. HF metadata F delat aditivne a neblokovat request-only MVP.
7. Po kazde fazi aktualizovat checklist a report.

## 12. Zdroje

### Repo zdroje

- `ARCHITECTURE.md`
- `docs/reference/workspace/speckit-principles.md`
- `.github/agents/Specifier.agent.md`
- `.github/agents/ImplementationPlanner.agent.md`
- `infra-local/llama-orchestrator/src/llama_orchestrator/benchmark.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/gui.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/hf_import.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/model_metadata.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/schema.py`
- `infra-local/llama-orchestrator/instances/00000050_e3c80dc2-4234-4156-b478-9194e3bd7d8f/config.json`

### Externi zdroje

- Hugging Face Hub HfApi docs - https://huggingface.co/docs/huggingface_hub/main/package_reference/hf_api, pristup 2026-06-04.
- llama.cpp multi-GPU command-line reference - https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md, pristup 2026-06-04.
- Lokalni `llama-server.exe --help` pro build `b9286`, spusteno 2026-06-04.
