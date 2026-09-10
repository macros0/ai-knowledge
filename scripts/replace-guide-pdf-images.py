"""Replace the embedded guide screenshots while preserving PDF text/layout."""

from pathlib import Path
import sys
import zlib

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, DecodedStreamObject


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "docs" / "OKF_User_Guide.pdf"
SCREENSHOTS = ROOT / "screenshots"

# PDF page number -> embedded image object names -> screenshot filenames.
IMAGE_MAP = {
    2: {"/X9": "screenshot-01.png"},
    4: {"/X16": "screenshot-02.png"},
    6: {"/X21": "screenshot-03.png"},
    7: {"/X24": "screenshot-04.png"},
    8: {"/X27": "screenshot-05.png", "/X28": "screenshot-06.png"},
    9: {"/X31": "screenshot-07.png"},
    10: {"/X34": "screenshot-08.png"},
    11: {"/X37": "screenshot-10.png", "/X38": "screenshot-11.png"},
    12: {"/X41": "screenshot-12.png"},
    13: {"/X44": "screenshot-13.png"},
}


def image_stream(path: Path) -> DecodedStreamObject:
    image = Image.open(path).convert("RGB")
    stream = DecodedStreamObject()
    raw = image.tobytes()
    stream.set_data(raw)
    stream.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(image.width),
            NameObject("/Height"): NumberObject(image.height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/FlateDecode"),
        }
    )
    stream._data = zlib.compress(raw, level=6)
    return stream


def main(output: Path) -> None:
    reader = PdfReader(str(PDF))
    if len(reader.pages) != 13:
        raise ValueError(f"Unexpected guide page count: {len(reader.pages)}")

    for page_number, objects in IMAGE_MAP.items():
        xobjects = reader.pages[page_number - 1]["/Resources"]["/XObject"]
        for object_name, filename in objects.items():
            if object_name not in xobjects:
                raise ValueError(f"Missing {object_name} on PDF page {page_number}")
            image_path = SCREENSHOTS / filename
            if not image_path.exists():
                raise FileNotFoundError(image_path)
            xobjects[NameObject(object_name)] = image_stream(image_path)

    output.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    with output.open("wb") as handle:
        writer.write(handle)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PDF)
