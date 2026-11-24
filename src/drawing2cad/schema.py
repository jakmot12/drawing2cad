"""Pydantic schemas for PDF extraction, CAD operation specs, and verification reports."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic.json_schema import SkipJsonSchema
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# PDF extraction models
# ---------------------------------------------------------------------------

DimensionType = Literal[
    "dlugosc",
    "srednica",
    "promien",
    "kat",
    "kwadrat",
    "gwint",
    "fazka",
]


class ExtractedDimension(BaseModel):
    """A single dimension token extracted deterministically from vector PDF text."""

    id: str = Field(..., description="Unique ID within the part, e.g. D1, D2")
    raw_text: str = Field(..., description="Original PDF text before font decoding")
    decoded_text: str = Field(..., description="Normalized text after Inventor font decoding")
    dim_type: DimensionType
    value: float | None = Field(default=None, description="Primary numeric value in mm or degrees")
    secondary_value: float | None = Field(
        default=None, description="Secondary value (e.g. chamfer angle 45 in 1x45°)"
    )
    multiplicity: int = Field(default=1, ge=1)
    thread_spec: str | None = Field(default=None, description="e.g. 'M10' or 'M10 LH'")
    bbox: tuple[float, float, float, float] = Field(
        ..., description="(x0, y0, x1, y1) in PDF points"
    )
    direction: tuple[float, float] = Field(
        default=(1.0, 0.0), description="Unit baseline vector of the text"
    )
    tolerance: str | None = Field(
        default=None, description="Tolerance or fit written with the dimension, e.g. '±0,1', 'H7', '+0,1/-0,2'"
    )
    reference: bool = Field(default=False, description="Reference dimension written in parentheses, e.g. (25)")
    type_uncertain: bool = Field(
        default=False,
        description="A symbol drawn as curves (not text) stands before the value and could not be read with "
                    "certainty: the dimension may be a diameter, square etc., not a length",
    )


class TitleBlock(BaseModel):
    """Metadata parsed from a drawing title block (tabelka rysunkowa)."""

    nazwa: str | None = None
    nr_rysunku: str | None = None
    skala: str | None = None
    autor: str | None = None
    arkusz: str | None = None
    instytucja: str | None = None


class BomItem(BaseModel):
    """A single row from an assembly Bill of Materials (wykaz części)."""

    poz: int
    ilosc: int
    nazwa: str
    nr_rysunku: str | None = None
    znormalizowana: bool = False


class PartRegion(BaseModel):
    """A single part area detected on a PDF page."""

    page_index: int
    drawing_no: str | None = None
    name: str | None = None
    bbox: tuple[float, float, float, float]
    dimensions: list[ExtractedDimension] = Field(default_factory=list)
    title_block: TitleBlock | None = None
    image_path: str | None = None
    raw_texts: list[str] = Field(default_factory=list)
    unrecognized_texts: list[str] = Field(
        default_factory=list, description="Texts that look like dimensions but could not be parsed"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Extraction problems that make the dimensions unusable (no vector text, inch units...)",
    )


class ExtractedDocument(BaseModel):
    """Complete extraction output for a vector PDF drawing."""

    source_pdf: str
    parts: list[PartRegion] = Field(default_factory=list)
    bom: list[BomItem] = Field(default_factory=list)
    assembly_title_block: TitleBlock | None = None
    assembly_dimensions: list[ExtractedDimension] = Field(default_factory=list)
    is_assembly: bool = False
    warnings: list[str] = Field(default_factory=list, description="Document-level extraction notes")


# ---------------------------------------------------------------------------
# Deterministic CAD operation library (AI selects blocks, never raw CadQuery)
# ---------------------------------------------------------------------------

Axis = Literal["X", "Y", "Z"]
Plane = Literal["XY", "XZ", "YZ"]
ThreadDirection = Literal["prawy", "lewy"]


class VertexFillet(BaseModel):
    """Fillet applied to a 2D profile corner at (x, z) or (x, y)."""

    point: tuple[float, float]
    radius: float = Field(..., gt=0)


class ProfileCircle(BaseModel):
    kind: Literal["circle"] = "circle"
    cx: float = 0.0
    cy: float = 0.0
    radius: float = Field(..., gt=0)


class ProfileRect(BaseModel):
    kind: Literal["rect"] = "rect"
    cx: float = 0.0
    cy: float = 0.0
    width: float = Field(..., gt=0)
    height: float = Field(..., gt=0)


class ProfileSlot(BaseModel):
    kind: Literal["slot"] = "slot"
    cx: float = 0.0
    cy: float = 0.0
    length: float = Field(..., gt=0)
    width: float = Field(..., gt=0)


class ProfilePolygon(BaseModel):
    kind: Literal["polygon"] = "polygon"
    points: list[tuple[float, float]] = Field(..., min_length=3)
    vertex_fillets: list[VertexFillet] = Field(default_factory=list)


Profile2D = Annotated[
    Union[ProfileCircle, ProfileRect, ProfileSlot, ProfilePolygon],
    Field(discriminator="kind"),
]


class PlytaZProfiluOp(BaseModel):
    """Flat extruded plate formed by union of 2D primitives."""

    op: Literal["plyta_z_profilu"] = "plyta_z_profilu"
    plane: Plane = "XY"
    thickness: float = Field(..., gt=0)
    centered_extrude: bool = False
    shapes: list[Profile2D] = Field(..., min_length=1)


class CeownikOp(BaseModel):
    """Sheet-metal bent U-channel (ceownik) with optional shaped side profile."""

    op: Literal["ceownik"] = "ceownik"
    length: float = Field(..., gt=0, description="Length along X")
    outer_width: float = Field(..., gt=0, description="Outer channel width along Y")
    height: float = Field(..., gt=0, description="Total height along Z")
    wall_thickness: float = Field(..., gt=0, description="Side wall thickness")
    web_thickness: float = Field(..., gt=0, description="Top/bottom web thickness")
    open_side: Literal["bottom", "top"] = "bottom"
    web_length: float | None = Field(
        default=None, gt=0, description="Web length along X (defaults to full length)"
    )
    side_profile: Profile2D | None = Field(
        default=None,
        description="Optional 2D profile in XZ plane shaping the side walls",
    )


class WalecOp(BaseModel):
    """Turned cylinder / shaft section along an axis."""

    op: Literal["walec"] = "walec"
    axis: Axis = "Z"
    diameter: float = Field(..., gt=0)
    length: float = Field(..., gt=0)
    offset: float = Field(default=0.0, description="Start position along axis")
    centered: bool = False


class CzopKwadratowyOp(BaseModel):
    """Square boss / drive tang with optional corner fillets."""

    op: Literal["czop_kwadratowy"] = "czop_kwadratowy"
    axis: Axis = "X"
    size: float = Field(..., gt=0)
    length: float = Field(..., gt=0)
    corner_radius: float = Field(default=0.0, ge=0)
    offset: float = Field(default=0.0, description="Start position along axis")


class OtworOp(BaseModel):
    """Circular hole(s) cut through (or to depth) on a given workplane."""

    op: Literal["otwor"] = "otwor"
    plane: Plane = "XZ"
    diameter: float = Field(..., gt=0)
    positions: list[tuple[float, float]] = Field(..., min_length=1)
    depth: float | None = Field(default=None, gt=0, description="None = through-all")


class OtworKwadratowyOp(BaseModel):
    """Square through-hole(s) with optional corner fillets."""

    op: Literal["otwor_kwadratowy"] = "otwor_kwadratowy"
    plane: Plane = "XY"
    size: float = Field(..., gt=0)
    corner_radius: float = Field(default=0.0, ge=0)
    positions: list[tuple[float, float]] = Field(default_factory=lambda: [(0.0, 0.0)])
    depth: float | None = Field(default=None, gt=0)


class WyciecieVOp(BaseModel):
    """V-notch cutout on a workplane."""

    op: Literal["wyciecie_v"] = "wyciecie_v"
    plane: Plane = "XZ"
    center_x: float = 0.0
    top_z: float = Field(..., description="Z coordinate of the notched edge")
    depth: float = Field(..., gt=0)
    angle_deg: float = Field(..., gt=0, lt=180)
    direction: Literal["down", "up"] = "down"


class ZaokraglenieKrawedziOp(BaseModel):
    """Fillet selected edges on the current solid."""

    op: Literal["zaokraglenie_krawedzi"] = "zaokraglenie_krawedzi"
    radius: float = Field(..., gt=0)
    selector: Literal["near_points", "face_edges"] = "near_points"
    axis: Axis = "Z"
    points: list[tuple[float, float]] = Field(
        default_factory=list,
        description="2D coordinates perpendicular to axis (e.g. (x, y) for axis='Z', (x, z) for axis='Y')",
    )
    face_selector: str | None = Field(
        default=None, description="CadQuery face selector string, e.g. '>Z or <Z'"
    )
    tolerance: float = Field(default=0.75, gt=0)


class FazkaOp(BaseModel):
    """Chamfer edges of specified end face(s)."""

    op: Literal["fazka"] = "fazka"
    size: float = Field(..., gt=0)
    angle_deg: float = Field(
        default=45.0, gt=0, lt=90,
        description="'size x angle': leg `size` on the selected face's edge, angle between chamfer and that leg's direction",
    )
    face_selectors: list[Literal[">X", "<X", ">Y", "<Y", ">Z", "<Z"]] = Field(
        ..., min_length=1
    )


Operation = Annotated[
    Union[
        PlytaZProfiluOp,
        CeownikOp,
        WalecOp,
        CzopKwadratowyOp,
        OtworOp,
        OtworKwadratowyOp,
        WyciecieVOp,
        ZaokraglenieKrawedziOp,
        FazkaOp,
    ],
    Field(discriminator="op"),
]


# ---------------------------------------------------------------------------
# Semantic bindings, threads, ambiguities, and full PartSpec
# ---------------------------------------------------------------------------

BindingTarget = Literal[
    "gabaryt",
    "dlugosc_odcinka",
    "otwor_srednica",
    "otwor_rozstaw",
    "otwor_wysokosc",
    "walec_srednica",
    "promien",
    "fazka",
    "kat_wyciecia",
    "glebokosc_wyciecia",
    "kat_sciecia",
    "grubosc",
    "kwadrat",
    "gwint_zewnetrzny",
    "gwint_wewnetrzny",
    "glebokosc_otworu",
]


class DimensionBinding(BaseModel):
    """Maps one extracted PDF dimension ID to a verifiable geometric feature."""

    dim_id: str
    target: BindingTarget
    expected_value: float | None = None
    secondary_value: float | None = None
    multiplicity: int = Field(default=1, ge=1)
    thread_spec: str | None = None
    description: str = ""


class ThreadAnnotation(BaseModel):
    """Thread metadata (geometry uses nominal cylinder; type/hand recorded here)."""

    spec: str = Field(..., description="e.g. 'M10' or 'M10 LH'")
    direction: ThreadDirection = "prawy"
    nominal_diameter: float = Field(..., gt=0)
    length: float | None = Field(default=None, gt=0)
    is_internal: bool = False
    location: str = ""


class Ambiguity(BaseModel):
    """Explicitly recorded drawing ambiguity or assumption (never silent)."""

    id: str
    description: str
    assumption: str
    severity: Literal["blad_rysunku", "ostrzezenie", "info"] = "ostrzezenie"
    affected_dimensions: list[str] = Field(default_factory=list)
    # who reported it; set by the verifier, never shown to or trusted from the AI
    source: SkipJsonSchema[Literal["ai", "program"]] = "ai"


class PartSpec(BaseModel):
    """Structured JSON specification produced by AI and executed deterministically."""

    drawing_no: str
    name: str
    part_type: Literal["plaska", "blacha_gieta", "toczona"]
    operations: list[Operation] = Field(..., min_length=1)
    dimension_bindings: list[DimensionBinding] = Field(default_factory=list)
    threads: list[ThreadAnnotation] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Verification & report models
# ---------------------------------------------------------------------------

CheckStatus = Literal["OK", "BŁĄD", "NIEPEWNE"]


class DimensionCheck(BaseModel):
    dim_id: str
    raw_text: str
    target: str
    expected: str
    measured: str
    status: CheckStatus
    message: str = ""


class ViewCheck(BaseModel):
    """Result of comparing one drawn view with the model's projection."""

    view_index: int
    ok: bool
    uncertain: bool = False
    projection: str = ""
    scale: float | None = None
    message: str = ""


class VerificationReport(BaseModel):
    drawing_no: str
    name: str
    status: CheckStatus
    passed: bool
    bounding_box_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    volume_mm3: float = 0.0
    is_valid_solid: bool = False
    checks: list[DimensionCheck] = Field(default_factory=list)
    view_checks: list[ViewCheck] = Field(default_factory=list)
    unused_dimensions: list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    threads: list[ThreadAnnotation] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    attempts: int = 1
    step_path: str | None = None
    image_path: str | None = None
