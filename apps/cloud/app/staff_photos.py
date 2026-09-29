from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener


register_heif_opener()


ALLOWED_STAFF_PHOTO_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
}


def normalise_staff_photo(
    content: bytes,
    filename: str,
) -> tuple[bytes, str, str]:
    """
    Validate and normalise a staff photograph.

    HEIC/HEIF and all other supported formats are converted to
    orientation-corrected JPEG for reliable browser display.
    """

    original_name = Path(filename or "photo").name
    suffix = Path(original_name).suffix.lower()

    if suffix not in ALLOWED_STAFF_PHOTO_SUFFIXES:
        raise ValueError("unsupported_photo_type")

    try:
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)

            if image.mode not in ("RGB", "L"):
                if "A" in image.getbands():
                    background = Image.new(
                        "RGB",
                        image.size,
                        "white",
                    )
                    alpha = image.getchannel("A")
                    background.paste(
                        image.convert("RGB"),
                        mask=alpha,
                    )
                    image = background
                else:
                    image = image.convert("RGB")

            if image.mode != "RGB":
                image = image.convert("RGB")

            # Prevent unnecessarily huge phone photographs being
            # stored while retaining ample resolution for staff use.
            image.thumbnail(
                (2400, 2400),
                Image.Resampling.LANCZOS,
            )

            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=90,
                optimize=True,
            )

    except Exception as exc:
        raise ValueError("invalid_photo") from exc

    stem = Path(original_name).stem[:200] or "photo"

    return (
        output.getvalue(),
        f"{stem}.jpg",
        "image/jpeg",
    )
