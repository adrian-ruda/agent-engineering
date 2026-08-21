#!/usr/bin/env python3
"""
sdd_status.py — SDD status engine machine-readable

Emite el estado AUTORITATIVO de un change SDD como JSON estructurado, para que un
orquestador de agentes y sus hooks ruteen por TOKEN-MÁQUINA (`nextRecommended`) en
vez de inferir la fase desde prosa con regex. Mata la reincidencia "teatro SDD
bajo presión" y "validate-sin-smoke": un blocker es un campo estructurado, no una
frase que se puede pasar por alto.

Regla de oro: tratar este JSON como autoritativo sobre la inferencia de prompt.
`nextRecommended` es un token acotado para ruteo, NO prosa humana — la prosa va
en `blockedReasons[]`.

Uso:
  python3 engine/sdd_status.py <change-name>                           # JSON completo
  python3 engine/sdd_status.py <change-name> --field nextRecommended   # un campo
  python3 engine/sdd_status.py --list                                  # changes en vuelo
  python3 engine/sdd_status.py --base <dir> <change-name>              # otra raíz

Raíz de changes (precedencia): --base > $SDD_STATUS_BASE > ./sdd/changes

Exit: 0 OK · 1 change no existe · 2 uso inválido
"""
import json
import os
import re
import sys
from pathlib import Path

# ── Raíz de los changes: ÚNICO punto de acoplamiento con el repo host ───────
#
# Antes esto eran dos líneas fijas: `REPO = Path(__file__).parents[1]` más una
# subruta hardcodeada del árbol donde nació el engine. Cortas, pero eran
# exactamente las dos que impedían correrlo en cualquier otro repo: el módulo se
# creía dueño de su ubicación en disco.
#
# Ahora `BASE` es una variable de módulo con tres fuentes en precedencia
# explícita (--base > env > default relativo al cwd). Se deja como GLOBAL a
# propósito: los tests inyectan `sdd_status.BASE = tmp_path` y esa reasignación
# ES el contrato de inyección soportado, no un hack. Toda lectura de disco pasa
# por acá — no hay una segunda raíz hardcodeada escondida más abajo.
#
# El `sys.path.insert(REPO)` que acompañaba a esas líneas se eliminó por
# vestigial: el módulo solo importa stdlib (json/os/re/sys/pathlib/ast). No
# aportaba un solo import y sí el riesgo de shadowear un módulo del host con un
# archivo homónimo del repo que lo aloje.
DEFAULT_BASE = Path("sdd") / "changes"
BASE_ENV_VAR = "SDD_STATUS_BASE"


def resolve_base(cli_base=None):
    """Raíz de changes según precedencia: --base > $SDD_STATUS_BASE > default."""
    if cli_base:
        return Path(cli_base).expanduser()
    env = os.environ.get(BASE_ENV_VAR)
    if env:
        return Path(env).expanduser()
    return DEFAULT_BASE


BASE = resolve_base()

# Orden canónico de fases del ciclo SDD.
PHASES = ["proposal", "spec", "design", "tasks", "apply", "verify", "archive"]
# Mapa fase → archivo canónico que la evidencia.
PHASE_FILE = {
    "proposal": "proposal.md",
    "spec": "spec.md",
    "design": "design.md",
    "tasks": "tasks.md",
    "apply": "apply-report.md",
    "verify": "verify-report.md",
    "archive": "archive-report.md",
}

# Fases cuyos reports se escriben en múltiples pasadas con sufijo
# (apply-report-M0.md, apply-report-wu2.md, verify-report-tanda3.md, ...).
# Para estas fases, la ausencia del nombre canónico NO significa que la fase
# no ocurrió — hay que resolver por patrón antes de declarar "ready"/ausente.
PHASE_GLOB_FALLBACK = {"apply", "verify"}

# ── Layout de delta specs: adoptado de gentle-ai (2026-08-04) ───────────────
#
# El motor nativo de gentle-ai (`internal/sddstatus/status.go:1327 findSpecFiles`)
# resuelve los delta specs caminando RECURSIVAMENTE `changes/{change}/specs/` y
# aceptando todo archivo cuyo nombre sea EXACTAMENTE `spec.md`. Un `spec.md`
# plano en la raíz del change le es invisible: devuelve `artifactPaths.specs: []`,
# marca `specs: blocked` y colapsa `nextRecommended` a `spec`.
#
# Decisión (2026-08-04): nos adaptamos nosotros al motor nativo, no al revés.
# Este engine adopta el MISMO layout, SIN fallback al plano — un
# `spec.md` en la raíz del change ya NO cuenta como evidencia de la fase spec.
# Un solo layout, un solo comportamiento, los dos motores de acuerdo.
PHASE_TREE_WALK = {"spec"}
# Subdirectorio raíz donde vive el árbol de delta specs, relativo al change.
SPEC_TREE_ROOT = "specs"

# Fases cuya terminación NO se puede deducir de la existencia de su report:
# apply/verify solo están terminadas si además NO quedan tareas pendientes en
# tasks.md. Ver el bloque FALSO VERDE en compute() para la evidencia y el
# porqué del gate por `pending > 0`.
TASK_GATED_PHASES = {"apply", "verify"}


def nonempty(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


def _is_valid_evidence_file(p: Path) -> bool:
    """H-C (2026-08-04, review round 3): a candidate only counts as evidence
    if it is a real, non-empty, readable FILE — never raises. `is_file()` and
    `stat()` are both wrapped in try/except OSError: a homonymous directory
    (`verify-report-tanda3.md/`), a broken symlink, or a permission-denied
    entry must never crash the engine (IsADirectoryError/PermissionError),
    and must never silently count as clean evidence either — it is simply
    excluded from the candidate list, exactly like an absent file.
    """
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


# ── resolve_phase_candidates(): REDISEÑO (2026-08-04, round 3) ──────────────
#
# Tres rondas de review adversarial encontraron un agujero nuevo en esta
# función en CADA ronda, siempre en la misma raíz: intentaba inferir qué
# archivo es "evidencia válida" a partir de su NOMBRE (descarte por sufijo
# -old/-bak/-borrador/-draft/-wip, precedencia del canónico vía
# `Path.exists()`). Cada regla agregada abrió una zona gris nueva:
#   - H-A: un report viejo (`verify-report-old-real.md`) con `Status: blocked`
#     REAL quedaba invisible si otro sufijado salía limpio — el descarte por
#     nombre podía ocultar un blocker de verdad.
#   - H-B: en filesystems case-insensitive (macOS/APFS, el entorno real),
#     `VERIFY-REPORT.MD` era tratado como "el canónico" por `Path.exists()`
#     y, por la regla "el canónico gana solo él", descartaba siblings
#     sufijados con `Status: blocked` reales.
#   - H-D: la exclusión por segmento de nombre descartaba reports legítimos
#     que simplemente tenían la mala suerte de contener una de esas palabras.
#
# Fix (este rediseño): SIN heurísticas de nombre. Agregación pura y total —
# todo archivo que matchee el patrón `<stem>*.<ext>` de la fase (o el nombre
# canónico exacto para fases que no son multi-pasada) Y pase
# `_is_valid_evidence_file()` (H-C) es evidencia. `detect_blockers()` parsea
# TODOS los candidatos; cualquiera que señale falla produce un blocker que
# nombra el archivo REAL en disco (no el nombre canónico esperado).
#
# CONSECUENCIA ACEPTADA, deliberada: un report viejo o borrador que diga
# `Status: blocked` va a BLOQUEAR la fase. Es correcto — si hay un archivo
# en la carpeta del change diciendo que algo falló, la salida correcta es
# actualizarlo o borrarlo, no que el motor adivine cuál ignorar por su
# nombre. El engine obliga a que el registro en disco diga la verdad; no
# intenta ser más inteligente que el archivo que tiene enfrente.
def resolve_phase_candidates(change_dir: Path, phase: str) -> list[Path]:
    """Return ALL files that evidence `phase` — pure, total aggregation.

    Tree-walk phases (PHASE_TREE_WALK — spec): the delta specs live under
    `specs/{capability}/spec.md`; every file named exactly `spec.md` found
    recursively under `specs/` counts. Layout adoptado de gentle-ai, sin
    fallback al `spec.md` plano en la raíz.

    Other non multi-pass phases (proposal/design/tasks/archive): only the
    exact canonical filename counts, as before.

    Multi-pass phases (PHASE_GLOB_FALLBACK — apply/verify): every entry in
    `change_dir` whose name starts with the canonical stem and ends with the
    canonical extension (case-insensitively, so `VERIFY-REPORT.MD` is just
    another candidate, never special-cased) AND passes
    `_is_valid_evidence_file()` is returned. No discard-by-name, no
    canonical-wins-alone precedence — see module comment above for why.
    Returns [] if nothing evidences the phase.
    """
    canonical_name = PHASE_FILE[phase]

    # Fases con árbol de capabilities (spec): layout gentle-ai
    # `specs/{capability}/spec.md`, walk recursivo, filename exacto. Ver el
    # comentario de PHASE_TREE_WALK arriba. Sin fallback al plano.
    if phase in PHASE_TREE_WALK:
        specs_root = change_dir / SPEC_TREE_ROOT
        try:
            if not specs_root.is_dir():
                return []
            found = sorted(specs_root.rglob(canonical_name), key=lambda p: str(p))
        except OSError:
            return []
        # rglob en filesystems case-insensitive (macOS/APFS) devuelve también
        # `SPEC.MD`; gentle compara el nombre exacto, así que filtramos igual.
        return [p for p in found if p.name == canonical_name and _is_valid_evidence_file(p)]

    if phase not in PHASE_GLOB_FALLBACK:
        canonical = change_dir / canonical_name
        return [canonical] if _is_valid_evidence_file(canonical) else []

    if not change_dir.exists():
        return []
    stem, _, ext = canonical_name.rpartition(".")
    stem_l, suffix_l = stem.lower(), f".{ext.lower()}" if ext else ""
    canonical_l = canonical_name.lower()
    try:
        entries = sorted(change_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    candidates = []
    for entry in entries:
        name_l = entry.name.lower()
        if not name_l.endswith(suffix_l):
            continue
        # Boundary guard: a candidate counts only if its name is exactly the
        # canonical filename, or the canonical stem followed by the `-`
        # separator (apply-report-M0.md, verify-report-tanda3.md). A bare
        # `startswith(stem_l)` would also match `verify-reporting-notes.md`
        # or `apply-reporte-cliente.md` — unrelated files that merely start
        # with the same 13 letters — and count them as phase evidence.
        if name_l != canonical_l and not name_l.startswith(f"{stem_l}-"):
            continue
        if _is_valid_evidence_file(entry):
            candidates.append(entry)
    return candidates


def resolve_phase_file(change_dir: Path, phase: str) -> Path | None:
    """Return ONE file that evidences `phase`, for presence checks only.

    Used by `present[phase]`/dependencies, where "is this phase present at
    all" only needs to know whether resolve_phase_candidates() is non-empty
    — which specific candidate is returned does not matter there. Content
    inspection that decides pass/fail (detect_blockers) MUST instead go
    through resolve_phase_candidates() and parse every candidate. Returns
    None if nothing evidences the phase.
    """
    candidates = resolve_phase_candidates(change_dir, phase)
    return candidates[0] if candidates else None


# R1-002 (2026-08-04, reviewer adversarial): cualquier bracket `[<marcador>]`
# en un list item o heading cuenta como una tarea marcada — SIN restringir la
# clase de caracteres aceptados dentro del corchete. Antes la clase era
# `[xX \-—]`: un marcador real del repo como `[~]` (ROLLED BACK — trabajo
# revertido/inconcluso, ver `archive/2026-05-21-security-hardening/tasks.md`
# T-2.2..T-2.5, T-5.2) no matcheaba, Y la guarda de "sin corchete" del defecto
# 3 (`(?!\[)`) lo excluía por SÍ tener un corchete → esa tarea desaparecía del
# conteo por completo, reportando `allComplete: true` con trabajo revertido.
# Clasificación fail-closed: "x"/"X" = completed · "-"/"—" = notApplicable ·
# CUALQUIER OTRA COSA (vacío "[ ]"/"[]", o desconocida como "~"/"!"/"?"/"/")
# = pending. Un marcador que el engine no reconoce NUNCA se asume terminado.

# List items (`- [x] ...`) keep the R1-002 behavior unchanged: the corchete
# solo ya es convención markdown estándar de checklist, sin ambigüedad.
# Grupo 2 = RESTO DE LA LÍNEA: lo necesita el gate anti-greenwash de N/A
# (B4) para exigir la justificación en la misma línea del marcador.
_LIST_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([^\]]*)\](.*)$", re.MULTILINE)

# Vocabulario de identificadores de tarea usado en el repo: T-<n>[.<n>...],
# TA-<n>, WU-<n>, M<n>-T<n>[.<n>...].
_TASK_ID_PATTERN = r"(?:T-\d+(?:\.\d+)*|TA-\d+|WU-\d+|M\d+-T\d+(?:\.\d+)*)"

# H2 (2026-08-04, reviewer adversarial — review #2): a diferencia del list
# item, un HEADING con corchete NO es inequívocamente una tarea — un heading
# de sección como `## [DRAFT] Notas de reunion` tiene bracket pero no es un
# marcador de tarea, y contaba igual como pending (falso positivo, inflaba
# total/pending). Para HEADINGS, exigimos que el corchete esté seguido
# inmediatamente por vocabulario de task-id (_TASK_ID_PATTERN) para contar
# como tarea marcada. Esto NO rompe el fix R1-002: un heading real como
# `### [~] T-2.2` sigue matcheando (tiene bracket + task-id) y sigue
# clasificando "~" como pending vía _classify_checkbox_marker.
_HEADING_TASK_CHECKBOX_RE = re.compile(
    rf"^\s*#{{1,6}}\s*\[([^\]]*)\]\s*(?:{_TASK_ID_PATTERN})\b(.*)$",
    re.MULTILINE,
)


def _classify_checkbox_marker(marker: str) -> str:
    """Fail-closed classification: unknown/blank markers are always pending."""
    m = marker.strip()
    if m in ("x", "X"):
        return "completed"
    if m in ("-", "—"):
        return "notApplicable"
    return "pending"  # blank ("", " ") or unknown ("~", "!", "?", "/", ...)


# ── B4 · GREENWASH DE UN TECLEO (2026-08-05, auditoría adversarial) ─────────
#
# Síntoma reproducido con fixture: 3 tareas REALES marcadas `[-]` → el motor
# emitía `nextRecommended: archive`, `applyAllowed: True`, `blockedReasons: []`.
# Convertir `- [ ]` en `- [-]` cuesta UN caracter y compra el verde entero.
# Peor: el propio `blockedReasons` del gate de tareas decía literal "o marcarlas
# [-] si no aplican" — el motor PUBLICITABA el atajo justo cuando la presión
# es máxima (a un paso de archivar).
#
# DISEÑO ELEGIDO — "N/A es una decisión trazable, no un escape barato":
#
#   (a) `total` INCLUYE `notApplicable`. El denominador tiene que reflejar
#       TODO el trabajo declarado: si excluye los N/A, tildar tres tareas como
#       no-aplica lleva el progreso de 3/6 a 3/3 sin haber hecho nada. Contar
#       N/A en el total NO los convierte en pendientes (`allComplete` sigue
#       dependiendo de `pending == 0`): descartar trabajo sigue siendo legítimo,
#       pero deja de ser invisible en el número que todos miran.
#
#   (b) Cada tarea marcada N/A debe llevar su justificación EN LA MISMA LÍNEA
#       (`— N/A: <razón>` / `no aplica: <razón>`). Las que no la llevan se
#       cuentan en `notApplicableUnjustified` y producen un `blockedReason`
#       explícito que además IMPIDE el ruteo a `archive` (ver compute()).
#
# Por qué la justificación va en tasks.md y no "en el report":
#   · es determinista (regex sobre una posición estructural fija), no una
#     heurística de nombres — la misma clase de inferencia que las rondas 1-3
#     de review ya obligaron a sacar de `resolve_phase_candidates()`;
#   · queda pegada a la decisión y viaja en el diff/git blame para siempre:
#     el revisor ve QUÉ se descartó y POR QUÉ en la misma línea;
#   · el atajo deja de ser gratis — cuesta escribir una razón que alguien va
#     a leer — sin volverse imposible.
#
# Por qué bloquea `archive` y NO `applyAllowed`:
#   · el premio del greenwash es declarar el change terminado; ahí es donde
#     tiene que doler;
#   · bloquear el apply violaría "un gate no puede bloquear su propia
#     reparación": el actor que escribe la justificación en tasks.md es
#     justamente la hoja APPLY.
_NA_JUSTIFICATION_RE = re.compile(
    r"\b(?:N/A|NA|NO\s+APLICA)\b\s*[:：—-]+\s*\S",
    re.IGNORECASE,
)


def _na_is_justified(rest_of_line: str) -> bool:
    """True if a `[-]`/`[—]` task line carries its justification inline."""
    return bool(_NA_JUSTIFICATION_RE.search(rest_of_line or ""))

# Defecto 3 (2026-08-04, hallado contra un change real de firma HMAC en edge):
# un heading de tarea SIN corchete alguno (`### T-1.7 — Smoke test ...`, ni
# `[x]` ni `[ ]` ni `[—]`) es trabajo real NO marcado, pero _CHECKBOX_RE lo
# ignora por completo (no hay `[...]` que matchear) — desaparece del conteo
# en vez de contar como pendiente. Con 12/32 tareas así, el engine reportaba
# `allComplete: true` con 12 tareas reales sin hacer: un falso verde producido
# por la propia herramienta que este trabajo busca erradicar.
#
# Heurística con dos guardas para no disparar de más:
#   1. Solo aplica DENTRO de documentos que ya usan el patrón checkbox-en-
#      heading (>=1 heading con `[x]`/`[ ]`/`[—]` en cualquier parte del doc).
#      Un tasks.md que nunca usa ese patrón no dispara nada — no inventamos
#      tareas donde el documento no las está señalando como tales.
#   2. El heading candidato debe matchear el vocabulario de IDs de tarea del
#      repo (_TASK_ID_PATTERN). Un heading de prosa como "## Guards (hook
#      pre-apply)" o "## Traceability: Tasks → Spec Requirements" NUNCA
#      cuenta, tenga o no un ID de tarea mencionado en el texto — el ID debe
#      estar inmediatamente después de los "#" (no en cualquier lugar de la
#      línea).
# Gate 1 (defecto 3): "¿este doc usa el patrón checkbox-en-heading?" — H2
# (2026-08-04, review #2) endureció esto para exigir bracket + task-id (ver
# _HEADING_TASK_CHECKBOX_RE arriba) en vez de cualquier bracket a secas, para
# no disparar el guard sobre headings de sección tipo `## [DRAFT] Notas`.
_HEADING_TASK_NO_CHECKBOX_RE = re.compile(
    rf"^\s*#{{1,6}}\s*(?!\[)(?:{_TASK_ID_PATTERN})\b",
    re.MULTILINE,
)


def _empty_progress(read_error: str | None = None) -> dict:
    return {
        "total": 0,
        "completed": 0,
        "pending": 0,
        "notApplicable": 0,
        "notApplicableUnjustified": 0,
        "allComplete": False,
        "readError": read_error,
    }


def _read_tasks_text(tasks_file: Path) -> tuple[str | None, str | None]:
    """Read tasks.md defensively — returns (text, readErrorClassName).

    B2 (2026-08-05, auditoría adversarial): esta lectura era cruda
    (`tasks_file.read_text(...)` a pelo). Un `tasks.md` que es un DIRECTORIO
    levanta `IsADirectoryError`; uno sin permisos levanta `PermissionError`.
    Cualquiera de las dos reventaba el motor entero → traceback → exit 1 →
    y el gate (que defaulteaba a `true`) APROBABA en silencio. Un bug de
    lectura se convertía en un apply aprobado.

    Se sigue el patrón que este mismo archivo ya usa en
    `_is_valid_evidence_file` (:83) y en la lectura de verify de
    `detect_blockers` (:452): `try/except OSError`, sin crash y sin verde
    silencioso — el error se REPORTA como estado degradado explícito.

    `(None, "FileNotFound")` cuando no existe (caso normal, no degradado):
    lo distingue el llamador. `(None, "<ClassName>")` para errores reales.
    """
    try:
        if not tasks_file.exists():
            return None, None
        return tasks_file.read_text(encoding="utf-8", errors="ignore"), None
    except OSError as exc:
        return None, exc.__class__.__name__


def task_progress(tasks_file: Path) -> dict:
    text, read_error = _read_tasks_text(tasks_file)
    if read_error is not None:
        # DEGRADADO EXPLÍCITO: el motor no sabe cuántas tareas hay. Los
        # contadores quedan en cero pero `readError` lo dice en voz alta —
        # compute() lo convierte en blocker duro para que ese cero NUNCA se
        # lea como "no queda nada pendiente".
        return _empty_progress(read_error)
    if text is None:
        return _empty_progress()

    done = todo = na = na_unjustified = 0

    def _tally(marker: str, rest: str) -> None:
        nonlocal done, todo, na, na_unjustified
        cls = _classify_checkbox_marker(marker)
        if cls == "completed":
            done += 1
        elif cls == "notApplicable":
            na += 1
            if not _na_is_justified(rest):
                na_unjustified += 1
        else:
            todo += 1

    for marker, rest in _LIST_CHECKBOX_RE.findall(text):
        _tally(marker, rest)
    for marker, rest in _HEADING_TASK_CHECKBOX_RE.findall(text):
        _tally(marker, rest)

    # Defecto 3: sumar headings de tarea sin corchete, solo si el doc ya
    # demostró usar el patrón checkbox-en-heading CON task-id (guarda 1, H2).
    if _HEADING_TASK_CHECKBOX_RE.search(text):
        todo += len(_HEADING_TASK_NO_CHECKBOX_RE.findall(text))

    # B4 (a): el total incluye los N/A — el denominador refleja TODO el trabajo
    # declarado. `allComplete` sigue dependiendo solo de `pending`, así que un
    # N/A legítimo no impide cerrar; simplemente deja de ser invisible.
    total = done + todo + na
    return {
        "total": total,
        "completed": done,
        "pending": todo,
        "notApplicable": na,
        "notApplicableUnjustified": na_unjustified,
        "allComplete": total > 0 and todo == 0,
        "readError": None,
    }


def _parse_verify_status(text: str) -> dict:
    """Parse structured verify-report signals — NOT global keyword scan.

    Extracts:
      - frontmatter `Status: success|partial|blocked|failed`
      - 4R Verdict: `PASSED|FAILED`
      - Bloqueantes 4R: `[list]` or `[]`
      - Findings count lines: `CRITICAL: N`, `WARNING: N`

    Returns dict with keys: frontmatter_status, four_r_verdict, four_r_blockers,
    critical_count, warning_count, is_structured (bool — whether frontmatter was found).
    """
    result = {
        "frontmatter_status": None,
        "four_r_verdict": None,
        "four_r_blockers": None,
        "critical_count": None,
        "warning_count": None,
        "is_structured": False,
    }
    if not text:
        return result

    # 1) Frontmatter Status (first heading/# comment block)
    fm_match = re.search(
        r"^Status:\s*(success|partial|blocked|failed)\s*$",
        text, re.MULTILINE | re.IGNORECASE
    )
    if fm_match:
        result["frontmatter_status"] = fm_match.group(1).lower()
        result["is_structured"] = True

    # 2) 4R Verdict line: **4R Verdict:** PASSED or FAILED
    four_r_match = re.search(
        r"\*\*4R Verdict:\*\*\s*(PASSED|FAILED)",
        text, re.IGNORECASE
    )
    if four_r_match:
        result["four_r_verdict"] = four_r_match.group(1).upper()

    # 3) Bloqueantes 4R: `**Bloqueantes 4R:** [...]`
    block_match = re.search(
        r"\*\*Bloqueantes 4R:\*\*\s*(\[.*?\])",
        text
    )
    if block_match:
        blockers_str = block_match.group(1)
        try:
            import ast
            parsed = ast.literal_eval(blockers_str)
            if isinstance(parsed, list):
                result["four_r_blockers"] = parsed
        except (ValueError, SyntaxError):
            result["four_r_blockers"] = None

    # 4) CRITICAL / WARNING count lines (findings summary — anchored to section context)
    # Must be the actual count, not prose mentioning the words.
    crit_match = re.search(r"^\s*[-*]\s*\*\*CRITICAL:\*\*\s*(\d+)\s*$", text, re.MULTILINE)
    if crit_match:
        result["critical_count"] = int(crit_match.group(1))

    warn_match = re.search(r"^\s*[-*]\s*\*\*WARNING:\*\*\s*(\d+)\s*$", text, re.MULTILINE)
    if warn_match:
        result["warning_count"] = int(warn_match.group(1))

    return result


def _verify_blocked(parsed: dict, filename: str = "verify-report.md") -> str | None:
    """Return a blocker reason string if the parsed verify-report signals a real failure,
    or None if the report is clean / mere historical mentions.

    H1 (2026-08-04, review #2): `filename` now names the CONCRETE file being
    parsed, not a hardcoded "verify-report.md" — when detect_blockers() has
    to parse multiple suffixed candidates (no canonical present), the
    resulting blocker message must point at the actual culprit file
    (e.g. "verify-report-tanda2.md Status=blocked — no archivar"), or the
    gate is "a wall with no door" (nowhere to go fix it). Default preserves
    the exact message shape for the canonical single-file case (and existing
    unit tests that call this with one positional arg).
    """
    # --- Structured mode: trust frontmatter + 4R + findings counts ---
    if parsed["is_structured"]:
        # Frontmatter non-success
        if parsed["frontmatter_status"] and parsed["frontmatter_status"] != "success":
            return f"{filename} Status={parsed['frontmatter_status']} — no archivar"
        # 4R failed
        if parsed["four_r_verdict"] == "FAILED":
            return f"{filename} 4R Verdict=FAILED — no archivar"
        # Non-empty 4R blockers
        if parsed["four_r_blockers"] is not None and len(parsed["four_r_blockers"]) > 0:
            return f"{filename} Bloqueantes 4R no vacíos: {parsed['four_r_blockers']} — no archivar"
        # CRITICAL count > 0
        if parsed["critical_count"] is not None and parsed["critical_count"] > 0:
            return f"{filename} tiene {parsed['critical_count']} hallazgos CRITICAL — no archivar"
        return None

    # --- Legacy mode: conservative fallback — keyword scan but anchored to headings ---
    # Only block if a CRITICAL finding block with count > 0 or explicit Status: failed/blocked.
    # This avoids false positives from historical prose.
    if parsed["frontmatter_status"] == "failed" or parsed["frontmatter_status"] == "blocked":
        return f"{filename} Status={parsed['frontmatter_status']} — no archivar"
    if parsed["critical_count"] is not None and parsed["critical_count"] > 0:
        return f"{filename} tiene {parsed['critical_count']} hallazgos CRITICAL — no archivar"
    return None


def detect_blockers(change_dir: Path) -> list[str]:
    reasons = []
    tasks = change_dir / "tasks.md"
    # B2: lectura defensiva (mismo patrón que `_is_valid_evidence_file` y que
    # la lectura de verify más abajo). Un tasks.md ilegible NO puede reventar
    # el motor — pero tampoco puede pasar como "sin blockers": si el oráculo
    # no puede leer el archivo que declara las guardas, la salida correcta es
    # BLOQUEAR, no asumir que están en verde.
    t, tasks_read_error = _read_tasks_text(tasks)
    if tasks_read_error is not None:
        reasons.append(
            f"tasks.md no se pudo leer ({tasks_read_error}) — el oráculo no puede "
            f"determinar el estado de las tareas ni las guardas declaradas; "
            f"reparar el archivo antes de aplicar o archivar"
        )
    if t is not None:
        if re.search(r"Decision needed before apply:\s*Yes", t, re.IGNORECASE):
            reasons.append("tasks.md declara 'Decision needed before apply: Yes' — decisión pendiente")
        if re.search(r"400-line budget risk:\s*High", t, re.IGNORECASE):
            reasons.append("tasks.md declara '400-line budget risk: High' — revisar scope antes de apply")
    # verify-report parsing: structured signals, NOT global keyword scan.
    # presence (`present["verify"]`) and content-parsing (this block) MUST
    # resolve the SAME file(s) — both go through resolve_phase_candidates().
    #
    # Fail-closed AGGREGATION (round-3 redesign, no discard/precedence by
    # name): EVERY valid candidate for "verify" is parsed here — any one
    # signaling failure produces a blocker naming that concrete file, real
    # name on disk. H-C: an unreadable file (permission denied, vanished
    # between listing and read, etc.) must NOT crash the engine and must NOT
    # be silently treated as clean — it becomes its own blocker instead.
    for verify in resolve_phase_candidates(change_dir, "verify"):
        try:
            v = verify.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            reasons.append(
                f"{verify.name} no se pudo leer ({exc.__class__.__name__}) — no archivar"
            )
            continue
        parsed = _parse_verify_status(v)
        blocker = _verify_blocked(parsed, verify.name)
        if blocker:
            reasons.append(blocker)
    return reasons


def compute(change: str) -> dict:
    change_dir = BASE / change
    if not change_dir.is_dir():
        return {"error": f"change '{change}' no existe en {BASE}"}

    present = {ph: resolve_phase_file(change_dir, ph) is not None for ph in PHASES}
    progress = task_progress(change_dir / "tasks.md")

    # FALSO VERDE (2026-08-04, evidencia real contra `client-site-v1`):
    # `dependencies` se calculaba SOLO por existencia de archivo — la mera
    # presencia de `apply-report.md` daba `apply: "all_done"` aunque tasks.md
    # tuviera 57 de 88 tareas sin hacer. `task_progress()` se computaba
    # DESPUÉS y no alimentaba nada. Un report escrito a mitad de camino
    # (pasada parcial, apply `partial`) le mentía al orquestador diciendo que
    # la fase estaba terminada.
    #
    # Fix: para las fases GATEADAS POR TAREAS (apply/verify) la presencia del
    # report es condición NECESARIA pero NO SUFICIENTE — si queda aunque sea
    # UNA tarea pendiente, la fase es `in_progress`, nunca `all_done`. Verify
    # entra en el mismo gate porque una verificación sobre trabajo inconcluso
    # tampoco puede declararse terminada.
    #
    # Se gatea por `pending > 0` (no por `allComplete`) a propósito: un change
    # sin tasks.md, o con un tasks.md sin checkboxes parseables, tiene
    # `allComplete: False` con `pending: 0` — ahí NO hay evidencia de trabajo
    # sin hacer y degradar la fase sería un falso ROJO. La regla solo dispara
    # con pendientes reales contados en disco.
    #
    # B2: un tasks.md ILEGIBLE (directorio homónimo, permisos, etc.) da
    # `pending: 0` porque el motor no pudo contar NADA — no porque no quede
    # trabajo. Ese cero jamás puede leerse como "fase terminada": mientras el
    # archivo no se pueda leer, las fases gateadas por tareas se degradan
    # igual que con pendientes reales. El blocker duro lo emite
    # detect_blockers() (ver arriba).
    tasks_unreadable = progress.get("readError") is not None
    tasks_pending = progress["pending"] > 0 or tasks_unreadable
    done = {
        ph: present[ph] and not (ph in TASK_GATED_PHASES and tasks_pending)
        for ph in PHASES
    }

    # dependencies: all_done (fase terminada de verdad) | in_progress (artefacto
    # presente pero la fase no está terminada — hay tareas pendientes) |
    # ready (previo done, este ausente) | blocked (algún previo sin terminar)
    deps = {}
    prev_ok = True
    for ph in PHASES:
        if done[ph]:
            deps[ph] = "all_done"
        elif present[ph]:
            deps[ph] = "in_progress"
        elif prev_ok:
            deps[ph] = "ready"
        else:
            deps[ph] = "blocked"
        prev_ok = prev_ok and done[ph]

    # `hard_blockers` = razones que REALMENTE bloquean (verify fallado, decisión
    # pendiente declarada, presupuesto en riesgo). Solo estas alimentan el ruteo
    # (`resolve-blockers`) y `applyAllowed` — ver `blocked_reasons` abajo para la
    # capa informativa, que NO puede bloquear el apply.
    hard_blockers = detect_blockers(change_dir)

    # nextRecommended: ruteo por fase (see spec contract).
    # Rutea sobre `done`, NO sobre `present`: si el token de ruteo saltara una
    # fase que `dependencies` reporta `in_progress`, el motor volvería a
    # producir el mismo falso verde por otra puerta (mandaba a `verify` un
    # change con 57 tareas sin hacer). Los tokens emitidos no cambian — son
    # los mismos de siempre; cambia CUÁNDO se emite cada uno.
    #
    # `present` va también al ruteo (DEADLOCK fix, ver _route_incomplete_phase):
    # `done` solo no alcanza para decidir A QUÉ fase degradada mandar, porque
    # apply y verify se degradan JUNTAS con el mismo `pending > 0`.
    nxt = _compute_next_recommended(done, hard_blockers, present)

    # OBSERVABILIDAD DEL GATE DE TAREAS (2026-08-04, review adversarial):
    # cuando `pending > 0` degrada una fase gateada CUYO ARTEFACTO YA EXISTE,
    # el motor emite un blocker EXPLÍCITO. Antes ese bloqueo era MUDO:
    # `blockedReasons: []` mientras `dependencies` decía `in_progress` y
    # `archive: blocked` — `cierre-gate.sh` imprimía "· change -> apply" sin
    # decir por qué, y `sdd-apply-gate.sh` mostraba una lista vacía.
    #
    # Va DESPUÉS del ruteo y NO entra en `hard_blockers` a propósito: si
    # entrara, `_compute_next_recommended` devolvería `resolve-blockers` y
    # `_compute_apply_allowed` daría False — rompiendo el contrato vigente de
    # los hooks ("un change con tareas pendientes DEBE poder seguir aplicando",
    # cubierto por test_field_contract_for_hooks_unchanged). Es una razón
    # INFORMATIVA de por qué la fase no cierra, no un veto al trabajo.
    #
    # Solo dispara si el artefacto de la fase ya está en disco: sin
    # apply-report todavía, tareas pendientes son simplemente "trabajo por
    # hacer" — el ruteo ya dice `apply` y no hay nada que explicar.
    blocked_reasons = list(hard_blockers)
    gated_open = [
        ph for ph in PHASES
        if ph in TASK_GATED_PHASES and present[ph] and not done[ph]
    ]
    if gated_open:
        # B4 (2026-08-05): este texto decía "o marcarlas [-] si no aplican".
        # El motor le estaba PUBLICITANDO el atajo de greenwash al lector,
        # exactamente en el momento de máxima presión (a un paso de archivar)
        # y exactamente al actor que puede tomarlo. Se saca la mención: quien
        # de verdad necesite descartar una tarea encontrará el mecanismo (y su
        # costo: la justificación inline) en el blocker de N/A de abajo, que
        # es donde corresponde — como requisito, no como sugerencia.
        blocked_reasons.append(
            f"{progress['pending']} tasks pendientes en tasks.md impiden cerrar "
            f"{'/'.join(gated_open)} — completarlas antes de archivar "
            f"(no bloquea seguir aplicando)"
        )

    # B4 (b): N/A sin justificación inline → blocker explícito + veto a
    # `archive`. No toca `applyAllowed`: el actor que escribe la justificación
    # en tasks.md es la propia hoja APPLY (un gate no puede bloquear su propia
    # reparación).
    na_unjustified = progress.get("notApplicableUnjustified", 0)
    if na_unjustified > 0:
        blocked_reasons.append(
            f"{na_unjustified} de {progress['notApplicable']} tareas marcadas N/A "
            f"([-]/[—]) no declaran POR QUÉ no aplican — agregá la justificación en "
            f"la misma línea (`— N/A: <razón>`). Descartar trabajo es una decisión "
            f"trazable, no un atajo para archivar"
        )
        if nxt == "archive":
            nxt = "resolve-blockers"

    # NOTE (round-3 redesign): there is no more `discardNotices` field — the
    # engine no longer discards evidence by filename, so there is nothing to
    # announce. See resolve_phase_candidates() docstring for the rationale.

    return {
        "schemaName": "agent-engineering.sdd-status",
        "schemaVersion": 1,
        "change": change,
        "dependencies": deps,
        "taskProgress": progress,
        "blockedReasons": blocked_reasons,
        "nextRecommended": nxt,
        "applyAllowed": _compute_apply_allowed(hard_blockers, present),
    }


# ── Known reason code taxonomies (fail-closed: unknown codes are blocking) ──

# Codes that are safe to archive with (purely informational / allow-only markers).
# Empty list also passes — no codes means clean.
_ALLOW_ARCHIVE_CODES: frozenset[str] = frozenset()  # no code is truly "safe" — empty only

# Codes that are definitively blocking — must halt apply/archive.
_BLOCKING_CODES: frozenset[str] = frozenset({
    "ROLE_CONFLICT",
    "BLOCKING_FINDING_OPEN",
    "RUNTIME_EVIDENCE_REQUIRED",
    "SCHEMA_UNSUPPORTED",
    "TARGET_MISMATCH",
    "LEDGER_MISMATCH",
    "EVIDENCE_MISSING",
    "EVIDENCE_INCONSISTENT",
    "BUDGET_EXHAUSTED",
    "HUMAN_DECISION_REQUIRED",
    "CORRECTION_BUDGET_EXCEEDED",
    "AUTHORITY_MISSING",
})

# Drift codes that are finalizable in reviewing/scope-changed contexts.
# These are NOT safe for archive with "allow" — they indicate a delta
# that needs review, but the review itself can be finalized.
_DRIFT_FINALIZABLE_CODES: frozenset[str] = frozenset({
    "TARGET_MISMATCH", "EVIDENCE_STALE",
    "POLICY_CHANGED", "LEDGER_MISMATCH",
})


def _reason_codes_allow_archive(reason_codes: list[str]) -> bool:
    """True if reasonCodes are safe to archive with (empty, or only informational).

    Fail-closed: any blocking code, any drift code, or ANY unknown code → False.
    Unknown codes are treated as blocking because the engine cannot determine
    their safety — an unrecognized code is potential drift/misconfiguration.
    """
    if not reason_codes:
        return True
    for rc in reason_codes:
        if rc.upper() in _ALLOW_ARCHIVE_CODES:
            continue
        # Unknown code → fail-closed
        return False
    return True


def _reason_codes_are_blocking(reason_codes: list[str], allowed: frozenset[str] | None = None) -> bool:
    """True if any reasonCode is in the known blocking set.

    Unknown codes are treated as blocking (fail-closed).
    If allowed is provided, codes in that set are NOT considered blocking
    even if they also appear in _BLOCKING_CODES. This allows drift codes
    (TARGET_MISMATCH, etc.) to be non-blocking in reviewing/scope-changed
    contexts while remaining blocking in archive context.
    """
    if not reason_codes:
        return False
    exempt = allowed or frozenset()
    for rc in reason_codes:
        if rc.upper() in exempt:
            continue
        if rc.upper() in _BLOCKING_CODES:
            return True
        # Unknown code → fail-closed: treat as blocking
        return True
    # All codes were exempt or matched no known blocking code
    return False




def _route_incomplete_phase(done: dict, present: dict | None = None) -> str:
    """Return the phase token to work on next — deadlock-safe.

    DEADLOCK (2026-08-04, review adversarial, reproducido con fixture real):
    el gate por tareas (TASK_GATED_PHASES) degrada apply Y verify JUNTAS con
    el mismo `pending > 0`. Con el ruteo ingenuo "primera fase no-done" eso
    devolvía SIEMPRE `apply`, así el change tuviera su `verify-report.md`
    escrito y limpio: `verify` no se recomendaba NUNCA. Y el bucle no lo podía
    romper nadie —
      · `sdd-verify` tiene tools Read/Grep/Glob/Bash: no puede tildar sus
        propios checkboxes;
      · `sdd-apply` sí puede tildarlos, pero las tareas que quedan son
        criterios de verificación (VG-*, SC-*, "cipher + ojo humano"): no son
        suyas para ejecutar;
      · `sdd-archive` (Task Completion Gate) hard-blockea cualquier `- [ ]`.

    Criterio general elegido — POSICIÓN POR ARTEFACTO PRESENTE, no por nombre
    de tarea: la fase más avanzada cuyo artefacto EXISTE en disco marca dónde
    está parado el change. Si esa fase no está terminada, ahí se rutea. Un
    report escrito es evidencia de que la fase ocurrió; rebobinar a una fase
    anterior que ya no puede hacer nada es exactamente el deadlock.

    Consecuencias (todas verificadas contra changes vivos):
      · apply-report Y verify-report presentes + tareas pendientes → `verify`
        (antes: `apply` para siempre).
      · verify-report presente pero SIN apply-report → `apply`. La fase apply
        nunca ocurrió: no hay nada que saltear (B3, ver la guarda en el
        cuerpo).
      · SIN verify-report + tareas pendientes → `apply`, igual que hoy. El fix
        del FALSO VERDE (`client-site-v1`, 31/88) NO se revierte.
      · Todo terminado → cae al ruteo estándar → `archive`.

    NO se distinguen tareas de implementación vs de verificación por prefijo
    (VG-/SC-/T-VERIFY): sería una heurística de NOMBRES sobre texto libre
    escrito por humanos — exactamente la clase de inferencia que las rondas 1-3
    de review ya obligaron a sacar de `resolve_phase_candidates()`. La presencia
    del artefacto es un hecho del filesystem; el prefijo de una tarea es una
    convención que nadie garantiza.

    `present=None` (o vacío) preserva el comportamiento previo exacto:
    primera fase no-done.
    """
    present = present or {}
    # 1) Fase más avanzada con artefacto en disco. Solo las fases gateadas por
    #    tareas pueden estar presentes-y-no-terminadas (para el resto,
    #    done == present), así que este scan solo cambia el ruteo en el caso
    #    del gate por tareas.
    for ph in reversed(PHASES):
        if not present.get(ph, False):
            continue
        if done.get(ph, False):
            break
        # ── B3 · DEADLOCK VIVO (2026-08-05, auditoría adversarial) ─────────
        #
        # El salto hacia adelante NO puede saltear una fase anterior cuyo
        # ARTEFACTO NO EXISTE. Sin esta guarda, un change con
        # `verify-report.md` pero SIN `apply-report.md` ruteaba a `verify`
        # aunque `dependencies` reportara `apply: ready` — violando el
        # principio que compute() declara explícitamente ("el ruteo nunca debe
        # saltear una fase que `dependencies` reporta no-terminada"). Como las
        # tasks pendientes solo se tildan aplicando, el change quedaba en
        # `verify` PARA SIEMPRE. Vivo en 2 de 12 changes al detectarlo
        # (`app-save-unification` 15 pending · `app-privacy-first` 1 pending):
        # ambos con verify-report, ninguno con apply-report.
        #
        # La guarda mira PRESENCIA, no `done`, a propósito: apply y verify se
        # degradan JUNTAS con el mismo `pending > 0`, así que exigir
        # `done[apply]` para poder rutear a `verify` reintroduciría el deadlock
        # original que este bloque vino a resolver. La distinción exacta es:
        #   · apply-report PRESENTE + tareas pendientes → la fase apply ocurrió,
        #     el change está parado en verify → rutear a `verify` (fix original);
        #   · apply-report AUSENTE → la fase apply NUNCA ocurrió, no hay nada
        #     que saltear → rutear a `apply` (este fix).
        if any(not present.get(p, False) for p in PHASES[: PHASES.index(ph)]):
            break
        return ph
    # 2) Ruteo estándar: primera fase sin terminar.
    for ph in PHASES:
        if not done.get(ph, False):
            return ph
    return "archive"


def _compute_next_recommended(
    done: dict,
    blockers: list[str],
    present: dict | None = None,
) -> str:
    """Phase routing for nextRecommended.

    `done` maps phase → "esta fase está TERMINADA" (not merely "its artifact
    exists"). For task-gated phases (TASK_GATED_PHASES) that means the report
    exists AND no task is left pending — see the FALSO VERDE block in
    compute(). Routing must never skip a phase that `dependencies` reports as
    `in_progress`, or the false green just moves to this token.

    Returns a canonical SDD routing token:
      - "resolve-blockers"    : blocked verify, 4R failed, declared hard blockers
      - "archive"             : every build phase done + no blockers
      - phase token           : standard phase routing for earlier stages
    """
    # ── Blockers always take priority ──
    if blockers:
        return "resolve-blockers"

    # ── Determine if all phases complete (spec-through-verify) ──
    # A change is "fully built" when apply and verify artifacts exist.
    # The verify artifact's content is already checked by detect_blockers
    # above (which returns blockers if verify signals failure).
    # NOTE: "archive" is excluded from this check — it IS the routing
    # result, not a precondition for routing decisions.
    build_phases = ["proposal", "spec", "design", "tasks", "apply", "verify"]
    all_built = all(done.get(ph, False) for ph in build_phases)

    if all_built:
        return "archive"

    # ── Non-terminal phases: standard routing ──
    # If apply/verify aren't done yet, route to the next phase
    return _route_incomplete_phase(done, present)


def _compute_apply_allowed(blockers: list[str], present: dict) -> bool:
    """Determine applyAllowed: no hard blockers + spec/design/tasks on disk."""
    base_allowed = (not blockers) and present.get("spec", False) and present.get("design", False) and present.get("tasks", False)
    if not base_allowed:
        return False
    return base_allowed


def _extract_base_flag(args):
    """Saca `--base <dir>` / `--base=<dir>` de argv y devuelve (resto, valor).

    Se parsea a mano en vez de con argparse porque el contrato de CLI ya
    existía (`<change>` posicional + `--field` + `--list`) y argparse lo
    hubiera roto: trata cualquier `<change>` que empiece con `-` como flag
    desconocido y aborta con exit 2 donde antes había un lookup válido.
    """
    rest, base = [], None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--base":
            if i + 1 >= len(args):
                print("--base necesita un directorio", file=sys.stderr)
                sys.exit(2)
            base = args[i + 1]
            i += 2
            continue
        if a.startswith("--base="):
            base = a.split("=", 1)[1]
            i += 1
            continue
        rest.append(a)
        i += 1
    return rest, base


def main():
    global BASE
    args, cli_base = _extract_base_flag(sys.argv[1:])
    BASE = resolve_base(cli_base)

    if not args or args[0] == "--list":
        # La zona de archivados es `changes/archive/` (antes `_archivado/`): se
        # renombró para que el motor nativo aguas arriba la excluya — filtra por
        # el nombre exacto `archive`, así que con `_archivado` la contaba como un
        # change más y devolvía `select-change` ambiguo.
        # El filtro de `_` se mantiene por defensa (dirs internos).
        changes = sorted(
            d.name for d in BASE.iterdir()
            if d.is_dir() and not d.name.startswith("_") and d.name != "archive"
        ) if BASE.exists() else []
        print("\n".join(changes))
        sys.exit(0)

    change = args[0]
    field = None
    if "--field" in args:
        i = args.index("--field")
        field = args[i + 1] if i + 1 < len(args) else None

    status = compute(change)
    if "error" in status:
        print(status["error"], file=sys.stderr)
        sys.exit(1)

    if field:
        val = status.get(field, "")
        print(json.dumps(val) if isinstance(val, (dict, list)) else val)
    else:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
