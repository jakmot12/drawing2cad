"""AI interpretation layer (vision LLM + structured JSON output + offline cache).

AI never invents numbers and never writes arbitrary CadQuery code.
It receives the cropped part drawing image and the deterministic list of
extracted PDF dimensions, and outputs a validated PartSpec JSON.

Providers: DeepSeek (default) or any OpenAI-compatible API, and Anthropic.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv
from pydantic import ValidationError

from drawing2cad.schema import Ambiguity, PartRegion, PartSpec

# Provider defaults; every value can be overridden in .env (see .env.example).
PROVIDERS: dict[str, dict] = {
    # DeepSeek exposes an OpenAI-compatible API. Model ids change often:
    # check https://api-docs.deepseek.com and set LLM_MODEL in .env.
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                 "key_env": "DEEPSEEK_API_KEY", "kind": "openai"},
    # Any other OpenAI-compatible endpoint (OpenRouter, Qwen, local server...).
    "openai_compat": {"base_url": None, "model": None, "key_env": "LLM_API_KEY", "kind": "openai"},
    "anthropic": {"base_url": None, "model": "claude-sonnet-5-5",
                  "key_env": "ANTHROPIC_API_KEY", "kind": "anthropic"},
}
DEFAULT_PROVIDER = "deepseek"
# Reasoning models (e.g. deepseek-flash) spend most of the budget on hidden reasoning
# before writing the JSON; 8k was measured as too small (~7k reasoning tokens).
DEFAULT_MAX_TOKENS = 64000

SYSTEM_PROMPT = """Jesteś inżynierem CAD interpretującym wektorowe rysunki techniczne 2D z Autodesk Inventor.
Twoim zadaniem jest zamiana rysunku pojedynczej części na ustrukturyzowaną specyfikację JSON (PartSpec).

OBOWIĄZKOWE ZASADY:
1. LICZBY POCHODZĄ WYŁĄCZNIE Z LISTY WYMIARÓW Z PDF. Nie wymyślaj i nie zmieniaj wartości liczbowych z rysunku.
2. Używaj wyłącznie operacji z biblioteki klocków:
   - plyta_z_profilu (płaskownik/płyta z sumy kształtów 2D: circle, rect, slot, polygon)
   - ceownik (blacha gięta U: length, outer_width, height, wall_thickness, web_thickness, open_side='bottom'|'top', opcjonalnie web_length i side_profile w płaszczyźnie XZ)
   - walec (element toczony wzdłuż osi X/Y/Z: diameter, length, offset, centered)
   - czop_kwadratowy (czop kwadratowy wzdłuż osi X/Y/Z: size, length, corner_radius, offset)
   - otwor (otwory przelotowe/nieprzelotowe na płaszczyźnie XY/XZ/YZ: diameter, positions)
   - otwor_kwadratowy (otwór kwadratowy z opcjonalnym corner_radius)
   - wyciecie_v (wycięcie V na płaszczyźnie XZ/XY/YZ: center_x, top_z, depth, angle_deg, direction)
   - zaokraglenie_krawedzi (fillet: selector='near_points' z axis i points, lub selector='face_edges' z face_selector)
   - fazka (chamfer na wskazanych ścianach face_selectors, np. ['>Z', '<Z'])
3. KAŻDY wymiar z listy wymiarów PDF MUSI zostać przypisany w `dimension_bindings` przez jego `dim_id`. Nieużyty wymiar oznacza błąd weryfikacji.
4. GWINTY modeluj jako walce lub otwory o średnicy nominalnej (np. M10 -> Ø10), a typ i kierunek gwintu ('prawy' lub 'lewy') zapisz w `threads`.
5. NIEJEDNOZNACZNOŚCI: jeśli na rysunku brakuje wymiaru (np. położenia otworów lub długości środnika) albo oznaczenie jest niepełne (np. R1 wskazane na jednej krawędzi), NIE zgaduj po cichu — opisz założenie w `ambiguities` z odpowiednią wagą ('blad_rysunku', 'ostrzezenie', 'info') i listą `affected_dimensions`.
6. UKŁAD WSPÓŁRZĘDNYCH KLOCKÓW (mm). Klocki bryłowe (plyta_z_profilu, ceownik, walec, czop_kwadratowy) są SUMOWANE z dotychczasową bryłą; otwor, otwor_kwadratowy, wyciecie_v ODEJMUJĄ materiał.
   - ceownik: X od -length/2 do +length/2 (WYŚRODKOWANY), Y od -outer_width/2 do +outer_width/2, Z od 0 do height.
     side_profile to obrys widoku z przodu we współrzędnych (x, z) TEGO SAMEGO układu, czyli x w [-length/2, length/2], z w [0, height];
     bryła = ceownik przecięty z pryzmatem tego obrysu. Np. ceownik length=80, height=20: prostokąt obrysu ma narożniki (-40,0), (40,0), (40,20), (-40,20).
     Gdy web_length < length i jest side_profile: ścianki mają kształt side_profile, a środnik długości web_length jest wyśrodkowany w x=0.
     Profil wychodzący poza x [-length/2, length/2] lub z [0, height] jest błędem budowy.
   - plyta_z_profilu: kształty we współrzędnych płaszczyzny: XY -> (x, y), wyciągnięcie od z=0 do z=thickness; XZ -> (x, z), wyciągnięcie od y=0 do y=-thickness;
     YZ -> (y, z). centered_extrude=true wyciąga symetrycznie ±thickness/2.
     rect: (cx, cy) to ŚRODEK, width wzdłuż pierwszej osi płaszczyzny. slot: (cx, cy) to środek, length to CAŁKOWITA długość razem z półkolami, wzdłuż pierwszej osi.
   - walec / czop_kwadratowy: oś pręta na osi układu; zajmuje od offset do offset+length wzdłuż dodatniego kierunku osi
     (centered=true: offset±length/2). Tak samo dla X, Y i Z.
   - otwor: positions to środki otworów we współrzędnych płaszczyzny (XZ -> (x, z), XY -> (x, y), YZ -> (y, z)); otwór przelotowy przez całą bryłę.
   - zaokraglenie_krawedzi near_points: punkty to położenie zaokrąglanych krawędzi rzutowane na płaszczyznę prostopadłą do `axis` (axis='Y' -> (x, z)).
   - Części symetryczne buduj symetrycznie względem początku układu. Gabaryty modelu muszą wynikać z wymiarów rysunku.
"""


# USD per 1M tokens (input cache miss, input cache hit, output incl. reasoning); override in .env.
DEFAULT_PRICES = {
    "deepseek": (0.30, 0.006, 1.20),
    "openai_compat": (1.00, 0.10, 4.00),
    "anthropic": (3.00, 0.30, 15.00),
}
DEFAULT_MAX_COST_USD = 0.50  # per program run
DEFAULT_EST_CALL_USD = 0.05  # used until real calls of this run are measured


class CostTracker:
    """Counts tokens and estimated cost of every API call, enforces a budget per run and
    appends each call to a ledger file (JSON lines) for comparison with the provider's bill."""

    def __init__(self, prices: tuple[float, float, float], max_cost: float | None, ledger: Path | None = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS, prompt_tokens_guess: int = 20000):
        self.price_in, self.price_cached, self.price_out = prices
        if max_cost is not None and not (math.isfinite(max_cost) and max_cost > 0):
            raise ValueError(f"Limit kosztów musi być dodatnią liczbą skończoną (podano {max_cost}).")
        self.max_cost = max_cost
        # worst case of one call: a full prompt without cache and the whole output budget
        self.worst_call = (prompt_tokens_guess * self.price_in + max_tokens * self.price_out) / 1e6
        self.ledger = ledger
        self.calls: list[dict] = []

    @property
    def total_usd(self) -> float:
        return sum(c["usd"] for c in self.calls)

    def estimate_call(self) -> float:
        seen = max((c["usd"] for c in self.calls), default=0.0)
        return max(seen, min(DEFAULT_EST_CALL_USD, self.worst_call)) if self.calls else self.worst_call

    def record_unknown(self, part: str, attempt: int, model: str) -> dict:
        """The provider returned no token usage: count the worst case, never zero."""
        entry = self.record(part, attempt, model, 0, 0, 0)
        entry["usd"] = round(self.worst_call, 5)
        entry["uwaga"] = "brak danych o tokenach: przyjęto najgorszy przypadek"
        return entry

    def check_budget(self) -> None:
        if self.max_cost is not None and self.total_usd + self.estimate_call() > self.max_cost:
            raise FatalInterpreterError(
                f"Limit kosztów: wydano ~{self.total_usd:.3f} USD, kolejne wywołanie (~{self.estimate_call():.3f} USD) "
                f"przekroczyłoby limit {self.max_cost:.2f} USD (LLM_MAX_COST / --max-cost)."
            )

    def record(self, part: str, attempt: int, model: str, prompt_tokens: int, cached_tokens: int,
               completion_tokens: int, reasoning_tokens: int = 0) -> dict:
        miss = max(prompt_tokens - cached_tokens, 0)
        usd = (miss * self.price_in + cached_tokens * self.price_cached + completion_tokens * self.price_out) / 1e6
        entry = {
            "czas": time.strftime("%Y-%m-%d %H:%M:%S"), "czesc": part, "proba": attempt, "model": model,
            "tokeny_wejscie": prompt_tokens, "tokeny_wejscie_cache": cached_tokens,
            "tokeny_wyjscie": completion_tokens, "tokeny_rozumowanie": reasoning_tokens, "usd": round(usd, 5),
        }
        self.calls.append(entry)
        if self.ledger is not None:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def summary(self) -> str:
        if not self.calls:
            return "Koszt AI: 0 wywołań API (wszystko z cache albo bez AI)."
        tin = sum(c["tokeny_wejscie"] for c in self.calls)
        tout = sum(c["tokeny_wyjscie"] for c in self.calls)
        treas = sum(c["tokeny_rozumowanie"] for c in self.calls)
        return (f"Koszt AI: {len(self.calls)} wywołań, {tin} tokenów wejścia, {tout} wyjścia "
                f"(w tym {treas} rozumowania), szacunkowo {self.total_usd:.3f} USD.")


class FatalInterpreterError(RuntimeError):
    """An error that retrying cannot fix (configuration, offline cache miss, rejected image,
    truncated answer). The repair loop stops immediately instead of paying for 3 attempts."""


class PartInterpreter(Protocol):
    """Protocol for any interpreter backend (live LLM API, manual specs, or test mock)."""

    def interpret(
        self,
        part: PartRegion,
        feedback_errors: list[str] | None = None,
        attempt: int = 1,
    ) -> PartSpec: ...


def build_user_prompt(
    part: PartRegion,
    feedback_errors: list[str] | None = None,
    attempt: int = 1,
) -> str:
    """Construct the structured text prompt containing extracted PDF dimensions and repair feedback."""
    dims_payload = [
        {
            "id": d.id,
            "text": d.decoded_text,
            "type": d.dim_type,
            "value": d.value,
            "secondary_value": d.secondary_value,
            "multiplicity": d.multiplicity,
            "thread_spec": d.thread_spec,
            "bbox": d.bbox,
        }
        for d in part.dimensions
    ]
    for item, d in zip(dims_payload, part.dimensions):  # only when present: old prompts stay identical
        if d.tolerance:
            item["tolerance"] = d.tolerance
        if d.reference:
            item["reference"] = True
        if d.type_uncertain:  # an unread symbol drawn as curves stands before the value (Ø, □ ...?)
            item["type_uncertain"] = True
    lines = [
        f"Część: {part.name or 'Nieznana'} (nr rysunku: {part.drawing_no or 'brak'})",
        f"Próba: {attempt}",
        "",
        "Wymiary wyciągnięte deterministycznie z tekstu wektorowego PDF (MUSISZ powiązać KAŻDY dim_id):",
        json.dumps(dims_payload, ensure_ascii=False, indent=2),
    ]
    if any(d.type_uncertain for d in part.dimensions):
        lines.extend([
            "",
            "Uwaga: przy wymiarach z \"type_uncertain\": true przed liczbą stoi symbol narysowany krzywymi, "
            "którego program nie odczytał (np. Ø albo □). Rodzaj wymiaru ustal z obrazu rysunku.",
        ])
    if feedback_errors:
        lines.extend(
            [
                "",
                "BŁĘDY WERYFIKACJI POPRZEDNIEJ PRÓBY — popraw specyfikację tak, aby usunąć wszystkie poniższe błędy:",
                *[f"- {err}" for err in feedback_errors],
            ]
        )
    return "\n".join(lines)


class ResponseCache:
    """File cache of real API responses, keyed ONLY by a hash of model, prompt and image.

    A changed prompt, image or model is a cache miss, never a silent reuse of an
    answer that was given to a different question.
    """

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)

    @staticmethod
    def key(prompt_text: str, image_path: str | None, model_id: str = "") -> str:
        h = hashlib.sha256()
        for part in (model_id.encode("utf-8"), prompt_text.encode("utf-8")):
            h.update(len(part).to_bytes(8, "big") + part)
        img = Path(image_path).read_bytes() if image_path and Path(image_path).is_file() else b""
        h.update(len(img).to_bytes(8, "big") + img)
        return h.hexdigest()[:16]

    def _path(self, part: PartRegion, digest: str) -> Path:
        safe = (part.drawing_no or "nieznany").replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"{safe}_{digest}.json"

    def load(self, part: PartRegion, prompt_text: str, model_id: str = "") -> PartSpec | None:
        path = self._path(part, self.key(prompt_text, part.image_path, model_id))
        if path.is_file():
            return PartSpec.model_validate_json(path.read_text(encoding="utf-8"))
        return None

    def save(self, part: PartRegion, prompt_text: str, spec: PartSpec, model_id: str = "") -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(part, self.key(prompt_text, part.image_path, model_id))
        path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        return path


class ManualSpecInterpreter:
    """Reads hand-written specs `<drawing_no>.json` from a directory.

    No AI involved: for testing the CAD blocks and the verifier, never for benchmarks.
    """

    def __init__(self, spec_dir: str | Path) -> None:
        self.spec_dir = Path(spec_dir)

    def interpret(
        self,
        part: PartRegion,
        feedback_errors: list[str] | None = None,
        attempt: int = 1,
    ) -> PartSpec:
        path = self.spec_dir / f"{part.drawing_no}.json"
        if not path.is_file():
            raise FatalInterpreterError(f"Brak ręcznej specyfikacji {path}.")
        return PartSpec.model_validate_json(path.read_text(encoding="utf-8"))


def _json_instructions() -> str:
    schema = json.dumps(PartSpec.model_json_schema(), ensure_ascii=False, sort_keys=True)
    return (
        "\n\nFORMAT ODPOWIEDZI: zwróć WYŁĄCZNIE jeden obiekt json zgodny z poniższym "
        "JSON Schema (bez komentarzy, bez markdown):\n" + schema
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


class LLMInterpreter:
    """Interprets a PartRegion with a vision LLM.

    Config (.env): LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL, LLM_VISION and the key
    variable of the chosen provider (DEEPSEEK_API_KEY / LLM_API_KEY / ANTHROPIC_API_KEY).
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        cache_dir: str | Path | None = None,
        offline_only: bool = False,
        api_key: str | None = None,
        base_url: str | None = None,
        vision: bool | None = None,
        max_cost: float | None = None,
    ) -> None:
        load_dotenv()
        self.provider = (provider or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip()
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"Nieznany dostawca LLM '{self.provider}'. Dostępni: {', '.join(PROVIDERS)}."
            )
        cfg = PROVIDERS[self.provider]
        self.kind = cfg["kind"]
        self.model = model or os.environ.get("LLM_MODEL") or cfg["model"]
        self.base_url = base_url or os.environ.get("LLM_BASE_URL") or cfg["base_url"]
        self.key_env = cfg["key_env"]
        self._api_key = (api_key or os.environ.get(self.key_env, "")).strip()
        if vision is None:
            vision = os.environ.get("LLM_VISION", "1").strip().lower() not in ("0", "false", "nie")
        self.vision = vision
        self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS") or DEFAULT_MAX_TOKENS)
        self.cache = ResponseCache(cache_dir) if cache_dir is not None else None
        self.offline_only = offline_only
        self._client = None
        prices = tuple(float(os.environ.get(k) or d) for k, d in zip(
            ("LLM_PRICE_IN", "LLM_PRICE_IN_CACHED", "LLM_PRICE_OUT"), DEFAULT_PRICES.get(self.provider, DEFAULT_PRICES["openai_compat"])))
        env_max = os.environ.get("LLM_MAX_COST")
        self.costs = CostTracker(
            prices,  # type: ignore[arg-type]
            max_cost if max_cost is not None else (float(env_max) if env_max else DEFAULT_MAX_COST_USD),
            ledger=Path(cache_dir) / "koszty.jsonl" if cache_dir is not None else None,
            max_tokens=self.max_tokens,
        )
        self._current = ("?", 0)

    @property
    def model_id(self) -> str:
        """Everything besides the user prompt and image that shapes the answer.
        The system prompt and schema are hashed in, so editing them invalidates the cache."""
        fixed = hashlib.sha256((SYSTEM_PROMPT + _json_instructions()).encode("utf-8")).hexdigest()[:12]
        return (
            f"{self.provider}|{self.base_url or ''}|{self.model}|vision={int(self.vision)}"
            f"|max_tokens={self.max_tokens}|sys={fixed}"
        )

    def _require_config(self) -> None:
        if not self.model:
            raise FatalInterpreterError("Nie ustawiono modelu: wpisz LLM_MODEL w .env.")
        if not self._api_key:
            raise FatalInterpreterError(
                f"Brak klucza {self.key_env} w środowisku lub pliku .env. "
                "Wpisz klucz w .env albo uruchom z --offline --cache-dir <katalog>."
            )
        if self.kind == "openai" and not self.base_url:
            raise FatalInterpreterError("Nie ustawiono LLM_BASE_URL w .env.")

    def interpret(
        self,
        part: PartRegion,
        feedback_errors: list[str] | None = None,
        attempt: int = 1,
    ) -> PartSpec:
        prompt_text = build_user_prompt(part, feedback_errors=feedback_errors, attempt=attempt)

        if self.cache is not None:
            cached = self.cache.load(part, prompt_text, self.model_id)
            if cached is not None:
                return cached

        if self.offline_only:
            raise FatalInterpreterError(
                f"Tryb offline: brak zapisanej odpowiedzi w cache dla części "
                f"{part.drawing_no or part.name} (próba {attempt}, model {self.model_id})."
            )

        self._require_config()
        self.costs.check_budget()
        self._current = (part.drawing_no or part.name or "?", attempt)
        image_b64 = None
        if self.vision and part.image_path and Path(part.image_path).is_file():
            image_b64 = base64.standard_b64encode(Path(part.image_path).read_bytes()).decode("ascii")

        if self.kind == "anthropic":
            spec = self._call_anthropic(prompt_text, image_b64)
        else:
            spec = self._call_openai_compat(prompt_text, image_b64)

        if image_b64 is None:
            # The model did not see the drawing: never pass this off as a full interpretation.
            spec.ambiguities.append(Ambiguity(
                id="AI-BEZ-OBRAZU",
                description="Model AI interpretował część bez obrazu rysunku (LLM_VISION=0 lub brak wycinka).",
                assumption="Interpretacja tylko z liczb i ich pozycji; wymaga sprawdzenia przez człowieka.",
                severity="ostrzezenie",
            ))

        if self.cache is not None:
            self.cache.save(part, prompt_text, spec, self.model_id)
        return spec

    def _call_openai_compat(self, prompt_text: str, image_b64: str | None) -> PartSpec:
        import openai

        if self._client is None:
            # no silent SDK retries: every paid call must pass the budget check
            self._client = openai.OpenAI(api_key=self._api_key, base_url=self.base_url, max_retries=0)
        content: list[dict] = []
        if image_b64:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"}})
        content.append({"type": "text", "text": prompt_text})
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + _json_instructions()},
                    {"role": "user", "content": content},
                ],
            )
        except openai.BadRequestError as exc:
            if image_b64 and "image" in str(exc).lower():
                raise FatalInterpreterError(
                    f"Model '{self.model}' odrzucił obraz ({exc}). Ustaw w .env model z obsługą "
                    "obrazów (LLM_MODEL) albo LLM_VISION=0 (części dostaną status NIEPEWNE)."
                ) from exc
            raise FatalInterpreterError(f"API odrzuciło zapytanie (400): {exc}") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise FatalInterpreterError(f"Odmowa dostępu do API ({exc}). Sprawdź klucz {self.key_env} w .env.") from exc
        except openai.NotFoundError as exc:
            raise FatalInterpreterError(f"API nie zna modelu '{self.model}' ({exc}). Sprawdź LLM_MODEL w .env.") from exc
        except openai.APIStatusError as exc:
            if getattr(exc, "status_code", None) == 402:
                raise FatalInterpreterError(f"Brak środków na koncie API ({exc}). Doładuj konto dostawcy.") from exc
            raise
        u = getattr(resp, "usage", None)
        if u is None:
            self.costs.record_unknown(self._current[0], self._current[1], self.model)
        else:
            pd = getattr(u, "prompt_tokens_details", None)
            cd = getattr(u, "completion_tokens_details", None)
            cached = getattr(u, "prompt_cache_hit_tokens", None) or (getattr(pd, "cached_tokens", 0) if pd else 0) or 0
            self.costs.record(self._current[0], self._current[1], self.model, int(u.prompt_tokens or 0), int(cached),
                              int(u.completion_tokens or 0), int((getattr(cd, "reasoning_tokens", 0) if cd else 0) or 0))
        choice = resp.choices[0]
        text = choice.message.content or ""
        if choice.finish_reason == "length":
            raise FatalInterpreterError(
                f"Odpowiedź AI ucięta na limicie {self.max_tokens} tokenów (model zużył je na "
                "rozumowanie lub długi JSON). Zwiększ LLM_MAX_TOKENS w .env."
            )
        try:
            return PartSpec.model_validate_json(_strip_fences(text))
        except ValidationError as exc:
            raise RuntimeError(f"Odpowiedź AI niezgodna ze schematem PartSpec: {exc}") from exc

    def _call_anthropic(self, prompt_text: str, image_b64: str | None) -> PartSpec:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._api_key, max_retries=0)
        content: list[dict] = []
        if image_b64:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": image_b64}})
        content.append({"type": "text", "text": prompt_text})
        try:
            response = self._anthropic_create(prompt_text, content)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise FatalInterpreterError(f"Odmowa dostępu do API ({exc}). Sprawdź klucz {self.key_env} w .env.") from exc
        except anthropic.NotFoundError as exc:
            raise FatalInterpreterError(f"API nie zna modelu '{self.model}' ({exc}). Sprawdź LLM_MODEL w .env.") from exc
        except anthropic.BadRequestError as exc:
            raise FatalInterpreterError(f"API odrzuciło zapytanie (400): {exc}") from exc
        except anthropic.APIStatusError as exc:
            if getattr(exc, "status_code", None) == 402:
                raise FatalInterpreterError(f"Brak środków na koncie API ({exc}). Doładuj konto dostawcy.") from exc
            raise
        return self._parse_anthropic(response)

    def _anthropic_create(self, prompt_text: str, content: list[dict]):
        return self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            tools=[{
                "name": "emit_part_spec",
                "description": "Zwróć strukturę PartSpec w JSON.",
                "input_schema": PartSpec.model_json_schema(),
            }],
            tool_choice={"type": "tool", "name": "emit_part_spec"},
            messages=[{"role": "user", "content": content}],
        )

    def _parse_anthropic(self, response) -> PartSpec:
        u = getattr(response, "usage", None)
        if u is None:
            self.costs.record_unknown(self._current[0], self._current[1], self.model)
        else:
            cached = int(getattr(u, "cache_read_input_tokens", 0) or 0)
            self.costs.record(self._current[0], self._current[1], self.model,
                              int(u.input_tokens or 0) + cached, cached, int(u.output_tokens or 0))
        if response.stop_reason == "max_tokens":
            raise FatalInterpreterError(
                f"Odpowiedź AI ucięta na limicie {self.max_tokens} tokenów. Zwiększ LLM_MAX_TOKENS w .env."
            )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_part_spec":
                try:
                    return PartSpec.model_validate(block.input)
                except ValidationError as exc:
                    raise RuntimeError(f"Odpowiedź AI niezgodna ze schematem PartSpec: {exc}") from exc
        raise RuntimeError("Model AI nie zwrócił ustrukturyzowanej odpowiedzi emit_part_spec.")
