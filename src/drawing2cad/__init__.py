"""drawing2cad: 2D technical drawing (vector PDF) -> verified 3D model (STEP)."""

from drawing2cad.blocks import BuildError, build_part
from drawing2cad.interpreter import LLMInterpreter, ManualSpecInterpreter, ResponseCache
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.pipeline import PipelineResult, process_part, process_pdf
from drawing2cad.schema import ExtractedDocument, PartSpec, VerificationReport
from drawing2cad.verifier import measure_solid, verify_part

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "BuildError",
    "LLMInterpreter",
    "ManualSpecInterpreter",
    "ExtractedDocument",
    "PartSpec",
    "PipelineResult",
    "ResponseCache",
    "VerificationReport",
    "build_part",
    "extract_pdf",
    "measure_solid",
    "process_part",
    "process_pdf",
    "verify_part",
]
